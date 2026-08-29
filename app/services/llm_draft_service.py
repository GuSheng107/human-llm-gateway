"""LLM 草稿生成服务（M7-B）。

任务工作台中用户选择 LLM 配置生成持久化草稿：
- 仅同协议：Chat/Responses -> openai_compatible LLM；Anthropic -> Anthropic LLM。
  跨协议转换在 M7-C 字段矩阵中实现，本阶段不开放。
- 使用配置的 base_url / api_key / 自定义 Header 调上游最小请求（非流式）。
- 上游响应解析为 ReplyDraft 后落库为 source=llm 的活动草稿，用户继续编辑后提交。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.db import begin_immediate_if_sqlite
from ..core.security import decrypt_secret
from ..domain.enums import (
    AuditAction,
    DraftSource,
    DraftState,
    InferenceProtocol,
    LLMProtocol,
    TaskState,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from ..repositories.llm_configs import LlmConfigRepository
from ..repositories.models import (
    LlmConfig,
    RequestTask,
    TaskDraft,
    User,
)
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository

_LLM_SECRET_PURPOSE = "llm-config"


# Inference protocol → 允许的 LLM 协议（M7-B 仅同协议生成）。
_INFERENCE_TO_LLM: dict[InferenceProtocol, LLMProtocol] = {
    InferenceProtocol.OPENAI_CHAT: LLMProtocol.OPENAI_COMPATIBLE,
    InferenceProtocol.OPENAI_RESPONSES: LLMProtocol.OPENAI_COMPATIBLE,
    InferenceProtocol.ANTHROPIC_MESSAGES: LLMProtocol.ANTHROPIC,
}


def _decrypt_config(row: LlmConfig) -> tuple[str, dict[str, str]]:
    try:
        secret = decrypt_secret(
            row.secret_ciphertext, get_settings().app_secret, _LLM_SECRET_PURPOSE
        )
    except Exception as exc:
        raise DomainError(
            DomainErrorCode.CONFLICT,
            "LLM Secret 解密失败，请重新保存配置",
            status_code=409,
        ) from exc
    headers: dict[str, str] = {}
    if row.headers_ciphertext:
        try:
            decoded = decrypt_secret(
                row.headers_ciphertext,
                get_settings().app_secret,
                _LLM_SECRET_PURPOSE,
            )
            headers = json.loads(decoded)
        except (ValueError, json.JSONDecodeError) as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "LLM 自定义 Header 解密失败，请重新保存配置",
                status_code=409,
            ) from exc
    return secret, headers


def _coerce_message_content(content: Any) -> str:
    """把上下文项的 content 统一为字符串（多模态数组简化为首段文本）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return ""
    return str(content) if content is not None else ""


def _build_chat_request(
    *,
    real_model: str,
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """OpenAI Chat Completions 请求体：把规范化 context 直接转 messages。

    system 指令注入首位（来自 normalized.instructions）。
    assistant 历史与 tool_calls 已包含在 context 中。
    """
    context = normalized.get("context") or []
    instructions = normalized.get("instructions")
    messages: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    for item in context:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant", "system", "tool"}:
            continue
        messages.append({"role": role, "content": _coerce_message_content(item.get("content"))})
    body: dict[str, Any] = {"model": real_model, "messages": messages}
    if normalized.get("tools"):
        body["tools"] = normalized["tools"]
    if normalized.get("tool_choice"):
        body["tool_choice"] = normalized["tool_choice"]
    for key, value in (normalized.get("options") or {}).items():
        body.setdefault(key, value)
    return body


def _build_anthropic_request(
    *,
    real_model: str,
    normalized: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    """Anthropic Messages 请求体。"""
    context = normalized.get("context") or []
    instructions = normalized.get("instructions")
    system_blocks = normalized.get("system_blocks")
    system_value: Any = None
    if isinstance(instructions, str) and instructions.strip():
        system_value = instructions
    elif isinstance(system_blocks, list) and system_blocks:
        system_value = system_blocks
    messages: list[dict[str, Any]] = []
    for item in context:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        messages.append(
            {
                "role": role,
                "content": _coerce_message_content(item.get("content")),
            }
        )
    body: dict[str, Any] = {
        "model": real_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system_value is not None:
        body["system"] = system_value
    if normalized.get("tools"):
        body["tools"] = normalized["tools"]
    for key, value in (normalized.get("options") or {}).items():
        body.setdefault(key, value)
    return body


def _parse_chat_response(payload: dict[str, Any]) -> ReplyDraft:
    """OpenAI Chat Completions 响应 → ReplyDraft。

    兼容字段：
    - choices[0].message.content（最终文本）
    - choices[0].message.reasoning_content（思考，M7-B 接受 chat protocol 兼容字段）
    - choices[0].message.tool_calls（数组，type=function 时取 function.name/arguments）
    """
    choices = payload.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游响应缺少 choices",
            status_code=502,
        )
    message = choices[0].get("message") or {}
    final_text = message.get("content") or ""
    if not isinstance(final_text, str):
        final_text = str(final_text)
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        reasoning = str(reasoning)
    tool_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        if call.get("type") == "function" and isinstance(call.get("function"), dict):
            fn = call["function"]
            arguments = fn.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments_obj = json.loads(arguments)
                except (ValueError, TypeError):
                    arguments_obj = {}
            else:
                arguments_obj = arguments
            tool_calls.append(
                {
                    "id": call.get("id") or fn.get("name", "tool"),
                    "name": fn.get("name", ""),
                    "arguments": arguments_obj,
                }
            )
        elif call.get("type") == "tool" or call.get("function") is None:
            tool_calls.append(
                {
                    "id": call.get("id", "tool"),
                    "name": (call.get("name") or call.get("function") or {}).get("name", "")
                    if isinstance(call.get("name"), dict)
                    else (call.get("name") or ""),
                    "arguments": call.get("input") or call.get("arguments") or {},
                }
            )
    return ReplyDraft(
        reasoning=reasoning or None,
        tool_calls=[
            {"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in tool_calls
        ],
        final_text=final_text or None,
    )


def _parse_anthropic_response(payload: dict[str, Any]) -> ReplyDraft:
    """Anthropic Messages 响应 → ReplyDraft。

    content blocks：
    - type=text → final_text
    - type=thinking → reasoning
    - type=tool_use → tool_calls（id / name / input）
    """
    content = payload.get("content")
    if not isinstance(content, list):
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游响应缺少 content",
            status_code=502,
        )
    final_text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text") or ""
            if text:
                final_text_parts.append(text)
        elif btype == "thinking":
            text = block.get("thinking") or block.get("text") or ""
            if text:
                reasoning_parts.append(text)
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or block.get("name", "tool"),
                    "name": block.get("name", ""),
                    "arguments": block.get("input") or {},
                }
            )
    final_text = "\n".join(final_text_parts).strip() or None
    reasoning = "\n".join(reasoning_parts).strip() or None
    return ReplyDraft(
        reasoning=reasoning,
        tool_calls=[
            {"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in tool_calls
        ],
        final_text=final_text,
    )


async def _post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """委托共享上游调用（app.services.llm_upstream），保留原名供既有调用/测试。"""
    from .llm_upstream import post_chat_completions

    return await post_chat_completions(
        base_url=base_url,
        api_key=api_key,
        request_body=request_body,
        extra_headers=extra_headers,
        timeout_seconds=timeout_seconds,
    )


async def _post_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """委托共享上游调用（app.services.llm_upstream），保留原名供既有调用/测试。"""
    from .llm_upstream import post_anthropic_messages

    return await post_anthropic_messages(
        base_url=base_url,
        api_key=api_key,
        request_body=request_body,
        extra_headers=extra_headers,
        timeout_seconds=timeout_seconds,
    )


class LlmDraftService:
    def __init__(self) -> None:
        self.llm_repo = LlmConfigRepository()
        self.tasks = TaskRepository()
        self.audit = AuditRepository()

    async def generate(
        self,
        session: Session,
        *,
        task: RequestTask,
        owner: User,
        llm_config_id: int,
    ) -> TaskDraft:
        begin_immediate_if_sqlite(session)
        # 1. 任务状态校验：仅 waiting_human 可生成
        if task.state is not TaskState.WAITING_HUMAN:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "任务已结束，不能再生成草稿",
                status_code=409,
                public_code="task_already_resolved",
            )
        # 2. 归属校验
        if task.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        # 3. LLM 配置所有权 + 启用
        cfg = self.llm_repo.get(session, llm_config_id)
        if cfg is None or cfg.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "LLM 配置不存在", status_code=404)
        if not cfg.is_enabled:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "LLM 配置已停用，无法生成草稿",
                status_code=400,
            )
        # 4. 同协议限制（M7-B 仅同协议）
        expected_llm_protocol = _INFERENCE_TO_LLM.get(task.protocol)
        if expected_llm_protocol is None or cfg.protocol is not expected_llm_protocol:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                (
                    f"任务协议 {task.protocol.value} 与 LLM 协议 "
                    f"{cfg.protocol.value} 不匹配，跨协议生成将在后续阶段开放"
                ),
                status_code=400,
            )
        # 5. 解密凭据 + 解析规范化请求
        secret, headers = _decrypt_config(cfg)
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, json.JSONDecodeError):
            normalized = {}
        # 6. 调上游
        try:
            if cfg.protocol is LLMProtocol.OPENAI_COMPATIBLE:
                body = _build_chat_request(real_model=cfg.real_model, normalized=normalized)
                upstream = await _post_chat_completions(
                    base_url=cfg.base_url,
                    api_key=secret,
                    request_body=body,
                    extra_headers=headers,
                    timeout_seconds=cfg.timeout_seconds,
                )
                draft = _parse_chat_response(upstream)
            else:
                body = _build_anthropic_request(
                    real_model=cfg.real_model,
                    normalized=normalized,
                    max_tokens=int(normalized.get("max_tokens") or 1024),
                )
                upstream = await _post_anthropic_messages(
                    base_url=cfg.base_url,
                    api_key=secret,
                    request_body=body,
                    extra_headers=headers,
                    timeout_seconds=cfg.timeout_seconds,
                )
                draft = _parse_anthropic_response(upstream)
        except DomainError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                f"上游响应解析失败: {exc.__class__.__name__}",
                status_code=502,
            ) from exc
        # 7. 落库为 LLM 来源草稿
        tool_calls_payload = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in draft.tool_calls
        ]
        row = TaskDraft(
            task_id=task.id,
            owner_user_id=owner.id,
            source=DraftSource.LLM,
            source_llm_config_id=cfg.id,
            state=DraftState.EDITING,
            reasoning_text=draft.reasoning,
            tool_calls_json=json.dumps(tool_calls_payload, ensure_ascii=False),
            final_text=draft.final_text,
        )
        session.add(row)
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.LLM_DRAFT_GENERATED,
            resource_type="task_draft",
            resource_id=str(row.id),
            actor_user_id=owner.id,
            owner_user_id=owner.id,
            metadata={
                "task_id": task.id,
                "llm_config_id": cfg.id,
                "fields": ["reasoning", "tool_calls", "final_text"],
            },
        )
        return row

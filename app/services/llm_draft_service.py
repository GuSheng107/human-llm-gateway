"""LLM 草稿生成服务（M7-B）。

任务工作台中用户选择 LLM 配置生成持久化草稿：
- 同协议直拼原始请求（多模态原样透传）；跨协议走 cross 矩阵转换。
- 生成契约：mode + generation_instruction + include_caller_system +
  excluded_context_item_ids + include_attachments + reasoning_seed。
- 上游响应解析为 ReplyDraft，经 CallerToolCallValidator 结构校验后落库为
  source=llm 的活动草稿，用户继续编辑后提交。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.constants import LLM_DEFAULT_MAX_TOKENS
from ..core.db import begin_immediate_if_sqlite
from ..core.logging import log_event
from ..core.security import decrypt_secret
from ..domain.enums import (
    ANTHROPIC_THINKING_BUDGETS,
    AuditAction,
    DraftSource,
    DraftState,
    InferenceProtocol,
    LLMProtocol,
    TaskState,
    ThinkingMode,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft, normalize_generation_instruction
from ..protocols import cross
from ..repositories.llm_configs import LlmConfigRepository
from ..repositories.models import LlmConfig, RequestTask, TaskDraft, User
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository
from . import llm_upstream
from .caller_tool_service import catalog_for_task, validate_structural

# ---------------------------------------------------------------------------
# 配置参数应用（采样默认 / extra_body / 思考模式）
# 说明：extra_body 与请求字段冲突时以请求体为准（setdefault 语义）。
# ---------------------------------------------------------------------------


def _apply_config(body: dict[str, Any], cfg: LlmConfig) -> dict[str, Any]:
    """把 LlmConfig 的 default_* / extra_body / thinking 应用到请求体。

    规则：
    1. extra_body 先用 setdefault 写入（请求体已显式提供的字段优先）；
    2. default_temperature / default_top_p / default_top_k 同理；
    3. 最大输出字段按目标协议写入（请求未带同义字段时）；
    4. 思考模式（三种上游协议都支持）：
       - OPENAI_RESPONSES: thinking_level -> reasoning.effort；
       - OPENAI_CHAT: thinking_level -> reasoning_effort；
       - ANTHROPIC_MESSAGES: enabled -> thinking.budget_tokens（按等级映射预算），
         disabled 不写 thinking 字段（模型默认）。
    """
    for key, value in (cfg.extra_body or {}).items():
        body.setdefault(key, value)
    if cfg.default_temperature is not None:
        body.setdefault("temperature", float(cfg.default_temperature))
    if cfg.default_top_p is not None:
        body.setdefault("top_p", float(cfg.default_top_p))
    if cfg.default_top_k is not None and cfg.protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        body.setdefault("top_k", cfg.default_top_k)
    if cfg.max_output_tokens is not None:
        if cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
            body.setdefault("max_output_tokens", cfg.max_output_tokens)
        elif cfg.protocol is LLMProtocol.ANTHROPIC_MESSAGES:
            body.setdefault("max_tokens", cfg.max_output_tokens)
        elif "max_tokens" not in body and "max_completion_tokens" not in body:
            body["max_completion_tokens"] = cfg.max_output_tokens
    if cfg.thinking_mode is ThinkingMode.ENABLED and cfg.thinking_level is not None:
        level = cfg.thinking_level
        if cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
            body.setdefault("reasoning", {"effort": level.value})
        elif cfg.protocol is LLMProtocol.OPENAI_CHAT:
            body.setdefault("reasoning_effort", level.value)
        elif cfg.protocol is LLMProtocol.ANTHROPIC_MESSAGES:
            budget = ANTHROPIC_THINKING_BUDGETS.get(level)
            if budget is not None:
                body.setdefault("thinking", {"type": "enabled", "budget_tokens": budget})
    return body


_LLM_SECRET_PURPOSE = "llm-config"

# 生成模式指令：mode=reasoning 时要求上游只输出推理过程；mode=reply 且
# 用户已写好思考链（reasoning_seed）时要求上游严格基于它直接给最终答复。
_REASONING_ONLY_INSTRUCTION = (
    "请只输出你对用户请求的详细逐步推理过程，不要输出最终答复、总结或结论段。"
)
_REPLY_FROM_SEED_TEMPLATE = (
    "以下思考过程已由人工确认，请严格基于它直接输出最终答复，不要再输出额外的推理过程。\n\n{seed}"
)
# 用户自定义生成引导（generation_instruction）：只表达“怎么生成”的方向性
# 要求，优先级低于协议与 Schema 约束、生成模式约束，高于调用方上下文。
_GENERATION_INSTRUCTION_TEMPLATE = (
    "以下是回复者对本次生成的引导要求（优先级低于协议、Schema 与生成模式约束），"
    "请在符合上述约束的前提下遵循：\n\n{instruction}"
)

# 生成模式（DraftGenerateInput.mode）
MODE_REASONING = "reasoning"
MODE_REPLY = "reply"
MODE_BOTH = "both"


# Inference protocol → 允许的 LLM 协议（M7-B 仅同协议生成）。
_INFERENCE_TO_LLM: dict[InferenceProtocol, LLMProtocol] = {
    InferenceProtocol.OPENAI_CHAT: LLMProtocol.OPENAI_CHAT,
    InferenceProtocol.OPENAI_RESPONSES: LLMProtocol.OPENAI_RESPONSES,
    InferenceProtocol.ANTHROPIC_MESSAGES: LLMProtocol.ANTHROPIC_MESSAGES,
}


def _decrypt_config(row: LlmConfig) -> str:
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
    return secret


def _coerce_message_content(content: Any) -> str:
    """把上下文项的 content 统一为字符串（跨协议文本提取用）。

    同协议路径不使用本函数：内容数组按协议原样透传（多模态不静默降级）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        return ""
    return str(content) if content is not None else ""


_MEDIA_PART_TYPES = frozenset(
    {"image", "image_url", "input_image", "input_audio", "audio", "file", "input_file", "document"}
)


def _content_has_media(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and str(part.get("type") or "") in _MEDIA_PART_TYPES
        for part in content
    )


def _normalized_has_media(normalized: dict[str, Any]) -> bool:
    context = normalized.get("context")
    if isinstance(context, list):
        for item in context:
            if isinstance(item, dict) and _content_has_media(item.get("content")):
                return True
    return False


def _strip_media_from_content(content: Any) -> Any:
    """剔除内容数组中的媒体块（include_attachments=False 时使用）。"""
    if not isinstance(content, list):
        return content
    return [
        part
        for part in content
        if not (isinstance(part, dict) and str(part.get("type") or "") in _MEDIA_PART_TYPES)
    ]


def _strip_media_from_normalized(normalized: dict[str, Any]) -> dict[str, Any]:
    context = normalized.get("context")
    if not isinstance(context, list):
        return normalized
    stripped = [
        {**item, "content": _strip_media_from_content(item.get("content"))}
        if isinstance(item, dict)
        else item
        for item in context
    ]
    return {**normalized, "context": stripped}


def _drop_caller_system(normalized: dict[str, Any], protocol_kind: str) -> dict[str, Any]:
    """include_caller_system=False：剔除调用方 system 指令（不进入上游）。"""
    prepared = {
        **normalized,
        "instructions": None,
        "system_blocks": None,
    }
    if protocol_kind == "chat":
        context = normalized.get("context")
        if isinstance(context, list):
            filtered = [
                item
                for item in context
                if not (
                    isinstance(item, dict)
                    and str(item.get("role") or "") in {"system", "developer"}
                )
            ]
            prepared["context"] = filtered
            prepared["messages"] = filtered
    return prepared


def _inject_generation_instruction(
    body: dict[str, Any], protocol: LLMProtocol, instruction: str
) -> None:
    """注入用户生成引导（在模式指令之后、调用方上下文之前的引导层级）。"""
    _inject_mode_instruction(body, protocol, instruction)


def _filter_context(
    normalized: dict[str, Any], excluded_indices: list[int] | None
) -> dict[str, Any]:
    """按稳定 ctx ID 解析出的下标剔除上下文条目（current_input 不可排除）。

    剔除不改变系统指令段（instructions / system_blocks）。
    """
    if not excluded_indices:
        return normalized
    context = normalized.get("context")
    if not isinstance(context, list):
        return normalized
    total = len(context)
    invalid = [i for i in excluded_indices if i < 0 or i >= total]
    if invalid:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"上下文下标越界: {invalid}",
            status_code=400,
        )
    excluded = set(excluded_indices)
    filtered = [item for index, item in enumerate(context) if index not in excluded]
    return {**normalized, "context": filtered}


def _inject_mode_instruction(body: dict[str, Any], protocol: LLMProtocol, instruction: str) -> None:
    """把生成模式提示注入上游请求体（shape 按协议）。"""
    if protocol is LLMProtocol.OPENAI_CHAT:
        messages = body.setdefault("messages", [])
        messages.insert(0, {"role": "system", "content": instruction})
    elif protocol is LLMProtocol.OPENAI_RESPONSES:
        existing = body.get("instructions")
        body["instructions"] = (
            f"{instruction}\n\n{existing}"
            if isinstance(existing, str) and existing.strip()
            else instruction
        )
    else:  # anthropic
        system = body.get("system")
        block = {"type": "text", "text": instruction}
        if isinstance(system, list):
            body["system"] = [block, *system]
        elif isinstance(system, str) and system.strip():
            body["system"] = f"{instruction}\n\n{system}"
        else:
            body["system"] = instruction


def _build_chat_request(
    *,
    real_model: str,
    normalized: dict[str, Any],
    cfg: LlmConfig | None = None,
) -> dict[str, Any]:
    """OpenAI Chat Completions 请求体：把规范化 context 直接转 messages。

    system 指令注入首位（来自 normalized.instructions）；内容数组（多模态）
    按协议原样透传，不静默降级为纯文本。assistant 历史 tool_calls 保留。
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
        message: dict[str, Any] = {"role": role, "content": item.get("content")}
        if role == "assistant" and isinstance(item.get("tool_calls"), list):
            message["tool_calls"] = item["tool_calls"]
            if message["content"] is None:
                message["content"] = None
        messages.append(message)
    body: dict[str, Any] = {"model": real_model, "messages": messages}
    if normalized.get("tools"):
        body["tools"] = normalized["tools"]
    if normalized.get("tool_choice"):
        body["tool_choice"] = normalized["tool_choice"]
    for key, value in (normalized.get("options") or {}).items():
        if key in {"tools", "tool_choice"}:
            continue
        body.setdefault(key, value)
    if cfg is not None:
        _apply_config(body, cfg)
    return body


def _build_anthropic_request(
    *,
    real_model: str,
    normalized: dict[str, Any],
    max_tokens: int,
    cfg: LlmConfig | None = None,
) -> dict[str, Any]:
    """Anthropic Messages 请求体（内容数组原样透传，多模态不降级）。"""
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
                "content": item.get("content"),
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
        if key in {"tools", "tool_choice"}:
            continue
        body.setdefault(key, value)
    if cfg is not None:
        _apply_config(body, cfg)
    return body


def _prepare_rebuild(normalized: dict[str, Any], mode: str) -> dict[str, Any]:
    """重建路径的规范化预处理：reasoning 模式不向上游提供工具。"""
    if mode != MODE_REASONING:
        return normalized
    options = dict(normalized.get("options") or {})
    options.pop("tools", None)
    options.pop("tool_choice", None)
    return {**normalized, "tools": None, "tool_choice": None, "options": options}


def _build_responses_request(
    *,
    real_model: str,
    normalized: dict[str, Any],
    mode: str,
    cfg: LlmConfig | None = None,
) -> dict[str, Any]:
    """OpenAI Responses 同协议请求体：input 项原样透传（多模态不降级）。

    使用已展开的规范化 context/input，绝不透传本平台代理 response ID。
    """
    prepared = _prepare_rebuild(normalized, mode)
    body: dict[str, Any] = {
        "model": real_model,
        "input": prepared.get("input") or [],
    }
    instructions = prepared.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        body["instructions"] = instructions
    if prepared.get("tools"):
        body["tools"] = prepared["tools"]
    if prepared.get("tool_choice"):
        body["tool_choice"] = prepared["tool_choice"]
    for key, value in (prepared.get("options") or {}).items():
        if key in {"tools", "tool_choice"}:
            continue
        body.setdefault(key, value)
    if cfg is not None:
        _apply_config(body, cfg)
    return body


def _parse_responses_response(payload: dict[str, Any]) -> ReplyDraft:
    """OpenAI Responses 响应 → ReplyDraft。

    output 数组：message（content[].output_text.text）、reasoning（summary[].text）、
    function_call（name / arguments JSON）。"""
    output = payload.get("output")
    if not isinstance(output, list):
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游响应缺少 output", status_code=502)
    final_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text") or ""
                    if text:
                        final_parts.append(text)
        elif item_type == "reasoning":
            for summary in item.get("summary") or []:
                if isinstance(summary, dict) and summary.get("text"):
                    reasoning_parts.append(summary["text"])
        elif item_type == "function_call":
            arguments = item.get("arguments") or ""
            if isinstance(arguments, str):
                try:
                    arguments_obj = json.loads(arguments)
                except (ValueError, TypeError):
                    arguments_obj = {}
            else:
                arguments_obj = arguments
            tool_calls.append(
                {
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "arguments": arguments_obj,
                }
            )
    return ReplyDraft(
        reasoning="".join(reasoning_parts) or None,
        tool_calls=tool_calls,
        final_text="".join(final_parts) or None,
    )


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
    timeout_seconds: float,
) -> dict[str, Any]:
    """委托共享上游调用；保留原名供既有外部引用（内部已直连 llm_upstream）。"""
    return await llm_upstream.post_chat_completions(
        base_url=base_url,
        api_key=api_key,
        request_body=request_body,
        timeout_seconds=timeout_seconds,
    )


async def _post_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """委托共享上游调用；保留原名供既有外部引用（内部已直连 llm_upstream）。"""
    return await llm_upstream.post_anthropic_messages(
        base_url=base_url,
        api_key=api_key,
        request_body=request_body,
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
        mode: str = MODE_BOTH,
        generation_instruction: str | None = None,
        include_caller_system: bool = True,
        excluded_context_item_ids: list[str] | None = None,
        include_attachments: bool = True,
        reasoning_seed: str | None = None,
    ) -> TaskDraft:
        """生成草稿（契约见 docs/API_CONTRACT.md §9）。

        mode:
        - ``reasoning``：只生成思考链（上游回复内容归入草稿 reasoning）。
        - ``reply``：只生成正式回复（可携带 ``reasoning_seed`` 作为人工已确认
          的思考依据；草稿 reasoning 保持用户手写内容不被覆盖）。
        - ``both``（默认）：思考链与回复都生成。

        ``generation_instruction`` 是一次生成操作的短生命周期引导（不落库、
        不进入最终回复），优先级低于协议/Schema 与生成模式约束。

        ``excluded_context_item_ids`` 为 RequestView 附带上下文的稳定 ctx ID
        （current_input 不可排除；未知 ID 400 ``invalid_context_item``）。

        ``include_caller_system`` / ``include_attachments`` 控制调用方 system
        指令与附件是否送上游；附件无法承载时返回 422
        ``attachment_not_supported``，不静默降级为纯文本。

        已存在未提交的 LLM 草稿时按模式合并更新，而不是拒绝（三模式需要
        允许「先生成思考链、再按它生成回复」的两次序列）。
        """
        if mode not in (MODE_REASONING, MODE_REPLY, MODE_BOTH):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, f"不支持的生成模式: {mode}", status_code=400
            )
        generation_instruction = normalize_generation_instruction(generation_instruction)
        task_id = task.id
        owner_id = owner.id
        # 1. 任务状态校验：仅 waiting_human 可生成
        if task.state is not TaskState.WAITING_HUMAN:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "任务已结束，不能再生成草稿",
                status_code=409,
                public_code="task_already_resolved",
            )
        # 1.5 幂等语义：已存在的 LLM 草稿在结尾按模式合并更新。
        from sqlalchemy import select as sa_select

        existing_id = session.execute(
            sa_select(TaskDraft.id).where(
                TaskDraft.task_id == task.id,
                TaskDraft.source == DraftSource.LLM,
                TaskDraft.state == DraftState.EDITING,
            )
        ).scalar_one_or_none()
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
        # 4. 解析规范化请求：ctx ID 解析 -> 上下文剔除 -> system/附件开关。
        from .request_view_service import RequestViewService

        view_service = RequestViewService()
        excluded_indices = view_service.resolve_excluded_context_ids(
            task, excluded_context_item_ids
        )
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, json.JSONDecodeError):
            normalized = {}
        protocol_kind = {
            InferenceProtocol.OPENAI_CHAT: "chat",
            InferenceProtocol.OPENAI_RESPONSES: "responses",
            InferenceProtocol.ANTHROPIC_MESSAGES: "anthropic",
        }[task.protocol]
        normalized = _filter_context(normalized, excluded_indices)
        if not include_caller_system:
            normalized = _drop_caller_system(normalized, protocol_kind)
        if not include_attachments:
            normalized = _strip_media_from_normalized(normalized)
        # 附件承载校验：同协议直拼/透传可携带多模态；跨协议或配置声明
        # 不支持图片输入时明确 422，绝不静默只取文本。
        has_attachments = _normalized_has_media(normalized)
        expected_llm_protocol = _INFERENCE_TO_LLM.get(task.protocol)
        if (
            has_attachments
            and include_attachments
            and (expected_llm_protocol is not cfg.protocol or not cfg.supports_image_input)
        ):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "所选 LLM 配置或跨协议路径无法承载请求中的附件",
                status_code=422,
                public_code="attachment_not_supported",
            )
        # 5. 构造目标协议请求体（同协议直拼；跨协议走 cross 矩阵，§12.6）
        raw_body: dict[str, Any] | None = None
        # 任何过滤/开关/guidance 都要求走规范化重建路径，不能直拼原始请求。
        if (
            excluded_indices is None
            and include_caller_system
            and include_attachments
            and expected_llm_protocol is cfg.protocol
        ):
            try:
                decoded_raw = json.loads(task.raw_payload_json)
                if isinstance(decoded_raw, dict):
                    raw_body = dict(decoded_raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_body = None
        # previous_response_id 是网关控制字段；即使上游同为 Responses，也必须
        # 使用已展开的规范化上下文，不能把本平台代理 ID 透传给上游。
        if raw_body is not None and not (
            cfg.protocol is LLMProtocol.OPENAI_RESPONSES
            and raw_body.get("previous_response_id") is not None
        ):
            body = raw_body
            body["model"] = cfg.real_model
            body["stream"] = False
            if mode == MODE_REASONING:
                body.pop("tools", None)
                body.pop("tool_choice", None)
            _apply_config(body, cfg)
        elif cfg.protocol is LLMProtocol.OPENAI_CHAT:
            if expected_llm_protocol is LLMProtocol.OPENAI_CHAT:
                normalized_prepared = _prepare_rebuild(normalized, mode)
                body = _build_chat_request(
                    real_model=cfg.real_model, normalized=normalized_prepared, cfg=cfg
                )
            else:
                body = cross.to_chat_request(normalized, cfg.real_model)
                _apply_config(body, cfg)
        elif cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
            if expected_llm_protocol is LLMProtocol.OPENAI_RESPONSES:
                body = _build_responses_request(
                    real_model=cfg.real_model, normalized=normalized, mode=mode, cfg=cfg
                )
            else:
                body = cross.to_responses_request(normalized, cfg.real_model)
                _apply_config(body, cfg)
        else:
            if expected_llm_protocol is LLMProtocol.ANTHROPIC_MESSAGES:
                body = _build_anthropic_request(
                    real_model=cfg.real_model,
                    normalized=_prepare_rebuild(normalized, mode),
                    max_tokens=int(normalized.get("max_tokens") or LLM_DEFAULT_MAX_TOKENS),
                    cfg=cfg,
                )
            else:
                body = cross.to_anthropic_request(normalized, cfg.real_model)
                _apply_config(body, cfg)
        # 5.5 提示注入。优先级固定：协议/Schema > 生成模式 > 用户生成引导 >
        # 调用方上下文。generation_instruction 先注入（在下），模式指令后
        # 注入（插到最前），最终顺序为 [mode, generation, caller system, ...]。
        if generation_instruction:
            _inject_generation_instruction(
                body,
                cfg.protocol,
                _GENERATION_INSTRUCTION_TEMPLATE.format(instruction=generation_instruction),
            )
        if mode == MODE_REASONING:
            _inject_mode_instruction(body, cfg.protocol, _REASONING_ONLY_INSTRUCTION)
        elif mode == MODE_REPLY and reasoning_seed and reasoning_seed.strip():
            _inject_mode_instruction(
                body, cfg.protocol, _REPLY_FROM_SEED_TEMPLATE.format(seed=reasoning_seed.strip())
            )
        # 6. 解密凭据并调上游（经 llm_upstream 模块属性调用，测试可统一 patch）
        secret = _decrypt_config(cfg)
        cfg_id = cfg.id
        cfg_protocol = cfg.protocol
        cfg_base_url = cfg.base_url
        cfg_timeout_seconds = cfg.timeout_seconds
        # 上游网络 I/O 前结束读取事务，避免 SQLite 在数十秒调用期间持锁。
        # 上游完成后重新取得写锁并复核任务与草稿状态。
        session.rollback()
        # 日志仅记录 generation_instruction 的存在性与长度/指纹指标，
        # 原始内容（可能含用户敏感描述）不写入日志（§11.6）。
        instruction_metrics: dict[str, Any] = {}
        if generation_instruction:
            instruction_metrics = {
                "generation_instruction_present": True,
                "generation_instruction_length": len(generation_instruction),
                "generation_instruction_sha256": hashlib.sha256(
                    generation_instruction.encode("utf-8")
                ).hexdigest(),
            }
        log_event(
            "info",
            "llm_draft.upstream_started",
            "开始调用 LLM 生成草稿",
            task_id=task_id,
            llm_config_id=cfg_id,
            protocol=cfg_protocol.value,
            mode=mode,
            include_caller_system=include_caller_system,
            include_attachments=include_attachments,
            **instruction_metrics,
        )
        try:
            if cfg_protocol is LLMProtocol.OPENAI_CHAT:
                upstream = await llm_upstream.post_chat_completions(
                    base_url=cfg_base_url,
                    api_key=secret,
                    request_body=body,
                    timeout_seconds=cfg_timeout_seconds,
                )
                draft = _parse_chat_response(upstream)
            elif cfg_protocol is LLMProtocol.OPENAI_RESPONSES:
                upstream = await llm_upstream.post_responses(
                    base_url=cfg_base_url,
                    api_key=secret,
                    request_body=body,
                    timeout_seconds=cfg_timeout_seconds,
                )
                draft = _parse_responses_response(upstream)
            else:
                upstream = await llm_upstream.post_anthropic_messages(
                    base_url=cfg_base_url,
                    api_key=secret,
                    request_body=body,
                    timeout_seconds=cfg_timeout_seconds,
                )
                draft = _parse_anthropic_response(upstream)
        except DomainError:
            log_event(
                "warning",
                "llm_draft.upstream_failed",
                "LLM 草稿上游调用失败",
                task_id=task_id,
                llm_config_id=cfg_id,
                protocol=cfg_protocol.value,
                mode=mode,
            )
            raise
        except (ValueError, KeyError, TypeError) as exc:
            log_event(
                "warning",
                "llm_draft.upstream_failed",
                "LLM 草稿上游响应解析失败",
                task_id=task_id,
                llm_config_id=cfg_id,
                protocol=cfg_protocol.value,
                mode=mode,
                error=exc.__class__.__name__,
            )
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                f"上游响应解析失败: {exc.__class__.__name__}",
                status_code=502,
            ) from exc
        # 5.9 模式后处理 + Caller Tool 结构校验（名称/参数 Schema/ID 唯一）。
        log_event(
            "info",
            "llm_draft.upstream_completed",
            "LLM 草稿上游调用完成",
            task_id=task_id,
            llm_config_id=cfg_id,
            protocol=cfg_protocol.value,
            mode=mode,
        )

        if mode == MODE_REASONING:
            # 只生成思考链：上游给的文本（无论走 reasoning 通道还是正文通道）
            # 统一归入 reasoning，不产出 final_text / tool_calls。
            draft = ReplyDraft(
                reasoning=(draft.reasoning or draft.final_text or ""),
                tool_calls=[],
                final_text=None,
            )
        elif mode == MODE_REPLY:
            # 只生成回复：丢弃上游附带的推理（若有人工思考链则保持草稿内手写内容）。
            draft = ReplyDraft(
                reasoning=None,
                tool_calls=draft.tool_calls,
                final_text=draft.final_text,
            )
        # LLM 草稿结果中的 Tool Call 必须引用当前请求声明的 Caller Tool；
        # 结构校验失败按上游生成无效处理（不静默丢弃、不落库）。
        if draft.tool_calls:
            try:
                validate_structural(
                    catalog_for_task(task), [c.model_dump() for c in draft.tool_calls]
                )
            except DomainError as exc:
                log_event(
                    "warning",
                    "llm_draft.tool_calls_invalid",
                    "LLM 草稿返回的 Tool Call 未通过 Caller Tool 校验",
                    task_id=task_id,
                    llm_config_id=cfg_id,
                    mode=mode,
                )
                raise DomainError(
                    DomainErrorCode.UPSTREAM_ERROR,
                    f"上游返回的 Tool Call 不符合当前请求声明的工具: {exc.message}",
                    status_code=502,
                    public_code="generated_tool_calls_invalid",
                ) from exc
        # 7. 落库前原子复核；调用期间若人工已回复或生成了草稿，拒绝晚到结果。
        begin_immediate_if_sqlite(session)
        current_task = session.get(RequestTask, task_id, with_for_update=True)
        if (
            current_task is None
            or current_task.owner_user_id != owner_id
            or current_task.state is not TaskState.WAITING_HUMAN
        ):
            log_event(
                "warning",
                "llm_draft.late_rejected",
                "LLM 草稿晚到被拒（任务已结束）",
                task_id=task_id,
                llm_config_id=cfg_id,
                mode=mode,
            )
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "任务已结束，不能保存晚到的 LLM 草稿",
                status_code=409,
                public_code="task_already_resolved",
            )
        current_cfg = self.llm_repo.get(session, cfg_id)
        if (
            current_cfg is None
            or current_cfg.owner_user_id != owner_id
            or not current_cfg.is_enabled
        ):
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "LLM 配置在调用期间已不可用",
                status_code=409,
            )
        existing_row: TaskDraft | None = None
        if existing_id is not None:
            existing_row = session.get(TaskDraft, existing_id)
            if existing_row is not None and existing_row.state is not DraftState.EDITING:
                existing_row = None
        tool_calls_payload = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in draft.tool_calls
        ]
        if existing_row is not None:
            # 按模式合并：reasoning 只改思考链；reply 只改正文与工具调用
            # （不覆盖用户手写思考链）；both 全部替换。
            if mode == MODE_REASONING:
                existing_row.reasoning_text = draft.reasoning
            elif mode == MODE_REPLY:
                existing_row.tool_calls_json = json.dumps(tool_calls_payload, ensure_ascii=False)
                existing_row.final_text = draft.final_text
            else:
                existing_row.reasoning_text = draft.reasoning
                existing_row.tool_calls_json = json.dumps(tool_calls_payload, ensure_ascii=False)
                existing_row.final_text = draft.final_text
            existing_row.source_llm_config_id = cfg_id
            existing_row.version = (existing_row.version or 0) + 1
            session.flush()
            row = existing_row
        else:
            row = TaskDraft(
                task_id=task_id,
                owner_user_id=owner_id,
                source=DraftSource.LLM,
                source_llm_config_id=cfg_id,
                state=DraftState.EDITING,
                reasoning_text=draft.reasoning if mode != MODE_REPLY else (reasoning_seed or None),
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
            actor_user_id=owner_id,
            owner_user_id=owner_id,
            metadata={
                "task_id": task_id,
                "llm_config_id": cfg_id,
                "mode": mode,
                "fields": ["reasoning", "tool_calls", "final_text"],
            },
        )
        log_event(
            "info",
            "llm_draft.persisted",
            "LLM 草稿已落库",
            task_id=task_id,
            llm_config_id=cfg_id,
            draft_id=row.id,
            mode=mode,
        )
        return row

"""指定 Caller Tool 参数生成服务（§7.4、§1.6）。

两条入口（回复工作台与 Web 小助手）复用同一套工具定义解析、JSON Schema
校验和错误码。生成结果只是一份可编辑建议：

- 不自动创建最终 Tool Call；
- 不自动保存草稿；
- 不自动提交回复；
- 更不执行工具（网关零执行）。

优先级固定：协议与 Schema 约束 > 生成模式约束 > 用户生成引导
（generation_instruction）> 调用方上下文。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..core.logging import log_event
from ..domain.enums import InferenceProtocol, LLMProtocol, TaskState
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import normalize_generation_instruction
from ..repositories.llm_configs import LlmConfigRepository
from ..repositories.models import LlmConfig, RequestTask, User
from . import llm_upstream
from .caller_tool_service import catalog_for_task, validate_tool_arguments
from .llm_draft_service import (
    _apply_config,
    _build_anthropic_request,
    _build_chat_request,
    _build_responses_request,
    _decrypt_config,
    _drop_caller_system,
    _filter_context,
    _normalized_has_media,
    _strip_media_from_normalized,
)
from .request_view_service import RequestViewService

_PROMPT_HEAD = "你是工具参数生成助手。请只为下方这一个工具生成 arguments。"
_JSON_ONLY_HINT = "只返回一个 JSON 对象（工具的 arguments），不要输出任何其他文字或代码围栏。"
_CURRENT_ARGUMENTS_TEMPLATE = "用户已填写的当前参数（请在其基础上补全或修正）：\n{current}"
_REPAIR_TEMPLATE = (
    "你上一次的输出未通过工具参数 Schema 校验：{errors}\n请修正后重新只返回一个"
    " 符合 Schema 的 JSON 对象（arguments），不要输出任何其他文字。"
)

# 宽松提取 JSON 对象（模型可能带围栏或前后说明）。
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class ToolArgumentGenerationService:
    def __init__(self) -> None:
        self.llm_repo = LlmConfigRepository()

    async def generate(
        self,
        session: Session,
        *,
        task: RequestTask,
        owner: User,
        tool_name: str,
        llm_config_id: int,
        generation_instruction: str | None = None,
        include_caller_system: bool = True,
        excluded_context_item_ids: list[str] | None = None,
        include_attachments: bool = True,
        current_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为指定 Caller Tool 生成参数建议；返回 {arguments, llm_config_id, warnings}。"""
        generation_instruction = normalize_generation_instruction(generation_instruction)
        task_id = task.id
        # 1. 任务归属与状态：仅所有者 + waiting_human。
        if task.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        if task.state is not TaskState.WAITING_HUMAN:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "任务已结束，不能生成工具参数",
                status_code=409,
                public_code="task_not_editable",
            )
        # 2. 从 CallerToolCatalog 精确查找工具。
        catalog = catalog_for_task(task)
        tool = catalog.get(tool_name)
        if tool is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具 {tool_name} 不在当前请求声明的 Caller Tool 中",
                status_code=400,
                public_code="caller_tool_not_declared",
            )
        if not tool.is_generatable:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具 {tool_name} 的类型不能映射为当前协议的 Tool Call",
                status_code=400,
                public_code="caller_tool_type_unsupported",
            )
        # 3. LLM 配置归属 + 启用 + 可解密（解密失败在 _decrypt_config 内抛 409）。
        cfg = self.llm_repo.get(session, llm_config_id)
        if cfg is None or cfg.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "LLM 配置不存在", status_code=404)
        if not cfg.is_enabled:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "LLM 配置已停用",
                status_code=400,
                public_code="llm_config_disabled",
            )
        # 4. 组装上下文（与草稿生成同一套开关语义）。
        view_service = RequestViewService()
        excluded_indices = view_service.resolve_excluded_context_ids(
            task, excluded_context_item_ids
        )
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, TypeError):
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
        has_attachments = _normalized_has_media(normalized)
        expected_llm_protocol = {
            InferenceProtocol.OPENAI_CHAT: LLMProtocol.OPENAI_CHAT,
            InferenceProtocol.OPENAI_RESPONSES: LLMProtocol.OPENAI_RESPONSES,
            InferenceProtocol.ANTHROPIC_MESSAGES: LLMProtocol.ANTHROPIC_MESSAGES,
        }[task.protocol]
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
        # 5. 构造只针对一个工具的提示（协议 > Schema > 引导 > 上下文）。
        prompt_parts: list[str] = [_PROMPT_HEAD]
        tool_block = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        prompt_parts.append("工具定义（arguments 必须符合 input_schema）：")
        prompt_parts.append(json.dumps(tool_block, ensure_ascii=False, indent=2))
        if current_arguments:
            prompt_parts.append(
                _CURRENT_ARGUMENTS_TEMPLATE.format(
                    current=json.dumps(current_arguments, ensure_ascii=False, indent=2)
                )
            )
        if generation_instruction:
            prompt_parts.append(
                "用户生成引导（优先级低于 Schema 约束；不得要求执行工具、伪造结果或突破 Schema）：\n"
                f"{generation_instruction}"
            )
        prompt_parts.append(_JSON_ONLY_HINT)
        prompt = "\n\n".join(prompt_parts)

        # 6. 构造上游请求体（按配置协议；上下文跟随调用方请求形态）。
        body = self._build_generation_body(cfg=cfg, task=task, normalized=normalized, prompt=prompt)
        secret = _decrypt_config(cfg)
        cfg_id = cfg.id
        cfg_protocol = cfg.protocol
        cfg_base_url = cfg.base_url
        cfg_timeout_seconds = cfg.timeout_seconds
        # 网络 I/O 前结束读取事务（与草稿生成同一锁纪律）。
        session.rollback()
        # 日志只记录引导的存在性/长度/指纹指标，不记录原文（§11.6）。
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
            "llm.tool_arguments.upstream_started",
            "开始调用 LLM 生成工具参数",
            task_id=task_id,
            tool_name=tool_name,
            llm_config_id=cfg_id,
            **instruction_metrics,
        )
        # 7. 调上游 -> 提取 JSON object -> 统一校验；失败修复一次。
        import time as _time

        started = _time.monotonic()
        warnings: list[str] = []
        last_error: str | None = None
        arguments: dict[str, Any] | None = None
        for attempt in range(2):
            upstream = await self._post_upstream(
                cfg_protocol, cfg_base_url, secret, body, cfg_timeout_seconds
            )
            raw_arguments = self._extract_json_object(upstream, cfg_protocol)
            try:
                validate_tool_arguments(catalog, tool_name, raw_arguments)
                arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
                break
            except DomainError as exc:
                last_error = exc.message
                if attempt == 0:
                    warnings.append("首次输出未通过 Schema 校验，已自动要求修正一次")
                    # 修复轮：把错误信息作为追加提示重试一次。
                    repair = _REPAIR_TEMPLATE.format(errors=last_error)
                    body = self._inject_followup(body, cfg_protocol, repair)
                else:
                    log_event(
                        "warning",
                        "llm.tool_arguments.generated_failed",
                        "LLM 两次输出均不符合 Caller Tool Schema",
                        task_id=task_id,
                        tool_name=tool_name,
                        llm_config_id=cfg_id,
                        duration_ms=int((_time.monotonic() - started) * 1000),
                    )
                    raise DomainError(
                        DomainErrorCode.UPSTREAM_ERROR,
                        f"LLM 两次生成的工具参数均未通过 Schema 校验（{last_error}）",
                        status_code=502,
                        public_code="generated_tool_arguments_invalid",
                    ) from exc
        if arguments is None:
            # 上游两次均未产出可解析 JSON object。
            log_event(
                "warning",
                "llm.tool_arguments.generated_failed",
                "LLM 未返回可解析的工具参数 JSON 对象",
                task_id=task_id,
                tool_name=tool_name,
                llm_config_id=cfg_id,
                error="unparseable_output",
            )
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "LLM 未返回可解析的工具参数 JSON 对象",
                status_code=502,
                public_code="generated_tool_arguments_invalid",
            )
        duration_ms = int((_time.monotonic() - started) * 1000)
        usage = self._extract_usage(upstream, cfg_protocol)
        # 8. 结果只返回前端：不保存草稿、不创建 Tool Call、不提交任务。
        log_event(
            "info",
            "llm.tool_arguments.generated",
            "LLM 工具参数建议已生成（仅返回前端，不落草稿）",
            task_id=task_id,
            tool_name=tool_name,
            llm_config_id=cfg_id,
            duration_ms=duration_ms,
            attempt=len(warnings) + 1,
            **(usage or {}),
        )
        return {
            "arguments": arguments,
            "llm_config_id": cfg_id,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 请求构造与响应解析
    # ------------------------------------------------------------------

    @staticmethod
    def _build_generation_body(
        *,
        cfg: LlmConfig,
        task: RequestTask,
        normalized: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        # 参数生成聚焦单工具：不携带 tools/tool_choice（避免上游自行调用工具）。
        prepared = {
            **normalized,
            "tools": None,
            "tool_choice": None,
            "options": {
                key: value
                for key, value in (normalized.get("options") or {}).items()
                if key not in {"tools", "tool_choice"}
            },
        }
        # 用“当前输入 + 附带上下文”作为 user 消息背景，参数生成 prompt 作为
        # system 指令；messages 形态与调用方请求一致（多模态原样透传）。
        if cfg.protocol is LLMProtocol.OPENAI_CHAT:
            body = _build_chat_request(real_model=cfg.real_model, normalized=prepared)
            messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
            messages.extend(body.get("messages") or [])
            body["messages"] = messages
        elif cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
            body = _build_responses_request(
                real_model=cfg.real_model, normalized=prepared, mode="both"
            )
            body["instructions"] = prompt
        else:
            body = _build_anthropic_request(
                real_model=cfg.real_model,
                normalized=prepared,
                max_tokens=int(normalized.get("max_tokens") or 1024),
            )
            body["system"] = prompt
        _apply_config(body, cfg)
        return body

    @staticmethod
    def _inject_followup(
        body: dict[str, Any], protocol: LLMProtocol, followup: str
    ) -> dict[str, Any]:
        """修复轮：把校验错误作为追加 user 指令注入。"""
        if protocol is LLMProtocol.OPENAI_CHAT:
            messages = list(body.get("messages") or [])
            messages.append({"role": "user", "content": followup})
            body["messages"] = messages
        elif protocol is LLMProtocol.OPENAI_RESPONSES:
            input_items = list(body.get("input") or [])
            input_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": followup}],
                }
            )
            body["input"] = input_items
        else:
            messages = list(body.get("messages") or [])
            messages.append({"role": "user", "content": followup})
            body["messages"] = messages
        return body

    @staticmethod
    async def _post_upstream(
        protocol: LLMProtocol,
        base_url: str,
        secret: str,
        request_body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if protocol is LLMProtocol.OPENAI_CHAT:
            return await llm_upstream.post_chat_completions(
                base_url=base_url,
                api_key=secret,
                request_body=request_body,
                timeout_seconds=timeout_seconds,
            )
        if protocol is LLMProtocol.OPENAI_RESPONSES:
            return await llm_upstream.post_responses(
                base_url=base_url,
                api_key=secret,
                request_body=request_body,
                timeout_seconds=timeout_seconds,
            )
        return await llm_upstream.post_anthropic_messages(
            base_url=base_url,
            api_key=secret,
            request_body=request_body,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _extract_json_object(upstream: dict[str, Any], protocol: LLMProtocol) -> Any:
        text = ToolArgumentGenerationService._extract_text(upstream, protocol)
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except (ValueError, TypeError):
            pass
        match = _JSON_OBJECT_PATTERN.search(cleaned)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_text(upstream: dict[str, Any], protocol: LLMProtocol) -> str:
        if protocol is LLMProtocol.OPENAI_CHAT:
            choices = upstream.get("choices")
            if isinstance(choices, list) and choices:
                content = choices[0].get("message", {}).get("content") or ""
                return content if isinstance(content, str) else ""
            return ""
        if protocol is LLMProtocol.OPENAI_RESPONSES:
            output = upstream.get("output")
            if not isinstance(output, list):
                return ""
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for block in item.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        parts.append(str(block.get("text") or ""))
            return "".join(parts)
        content = upstream.get("content")
        if isinstance(content, list):
            return "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""

    @staticmethod
    def _extract_usage(upstream: dict[str, Any], protocol: LLMProtocol) -> dict[str, Any]:
        usage = upstream.get("usage")
        if isinstance(usage, dict):
            return {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), int)
            }
        return {}

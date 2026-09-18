"""跨协议字段转换矩阵（docs/API_CONTRACT.md §12.6，M7-D）。

把任务的规范化请求转换为目标 LLM 协议（openai_chat / anthropic）的
请求体。每个字段只有四种处理：透传、等价转换、网关消费、拒绝 400；
禁止"忽略""尽量转换"或塞进 metadata。

支持转换：
- 系统指令 / 用户助手内容（文本与角色）
- 输出上限（max_tokens <-> max_completion_tokens / max_output_tokens）
- 采样参数（temperature / top_p）
- 停止序列（stop 字符串 <-> 单元素数组 <-> stop_sequences）
- 函数工具 Schema（function.parameters <-> input_schema）
- 工具选择（none/auto；required <-> any；指定函数 <-> tool{name}）
- 并行工具（parallel_tool_calls <-> disable_parallel_tool_use 取反）
- metadata（user / metadata.user_id 等价键）

拒绝 400 `unsupported_parameter`：
- reasoning 请求控制跨协议（thinking <-> reasoning）
- 结构化输出转 Anthropic（response_format / text.format）
- cache_control / prompt cache 类供应商专有
- service_tier 等计费层参数跨协议
- 托管工具（file search / computer use 等）
- 其余未知字段
"""

from __future__ import annotations

from typing import Any

from ..core.logging import log_event
from .cross_messages import (
    check_fields,
    messages_to_anthropic,
    messages_to_responses,
)
from .cross_messages import (
    context_to_chat_messages as _context_to_chat_messages,
)
from .cross_messages import (
    unsupported as _unsupported,
)
from .cross_tools import (
    parallel_allowed,
)
from .cross_tools import (
    tool_choice_to_anthropic as _tool_choice_to_anthropic,
)
from .cross_tools import (
    tool_choice_to_chat as _tool_choice_to_chat,
)
from .cross_tools import (
    tools_to_anthropic as _tools_to_anthropic,
)
from .cross_tools import (
    tools_to_chat as _tools_to_chat,
)

# ----------------------------------------------------------------------
# 采样 / 停止 / 输出上限 / metadata
# ----------------------------------------------------------------------


def _extract_options(normalized: dict[str, Any]) -> dict[str, Any]:
    return normalized.get("options") or {}


def _record_conversion(normalized: dict[str, Any], target: str) -> None:
    """字段处理摘要只含字段名/动作/结果，不包含调用方字段值。"""
    fields = {
        key: normalized[key]
        for key in (
            "context",
            "instructions",
            "system_blocks",
            "tools",
            "tool_choice",
            "max_tokens",
            "store",
            "stream",
        )
        if key in normalized
    }
    fields.update(_extract_options(normalized))
    actions = []
    for key, value in fields.items():
        action = "convert"
        if (
            value is None
            or key in {"stream_options", "stream"}
            or (key == "store" and target != "openai_responses")
        ):
            action = "consume"
        elif key in {"temperature", "top_p", "store"}:
            action = "passthrough"
        actions.append({"field": key, "action": action, "result": "accepted"})
    log_event(
        "info",
        "llm.protocol.converted",
        "跨协议字段转换完成",
        target_protocol=target,
        field_actions=actions,
    )


def _sample_params(normalized: dict[str, Any], *, max_temperature: float = 2) -> dict[str, Any]:
    options = _extract_options(normalized)
    result: dict[str, Any] = {}
    for key in ("temperature", "top_p"):
        if key in options and options[key] is not None:
            value = options[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise _unsupported(key, "must be numeric")
            limit = max_temperature if key == "temperature" else 1
            if not 0 <= value <= limit:
                raise _unsupported(key, "outside target protocol range")
            result[key] = value
    return result


def _stop_to_chat(normalized: dict[str, Any]) -> Any:
    options = _extract_options(normalized)
    if "stop" in options and options["stop"] is not None:
        return options["stop"]  # Chat 接受字符串或数组
    if "stop_sequences" in options and options["stop_sequences"] is not None:
        seq = options["stop_sequences"]
        if not isinstance(seq, list):
            raise _unsupported("stop_sequences", "must be array")
        return seq
    return None


def _stop_to_anthropic(normalized: dict[str, Any]) -> list[str] | None:
    options = _extract_options(normalized)
    if "stop_sequences" in options and options["stop_sequences"] is not None:
        seq = options["stop_sequences"]
        if not isinstance(seq, list):
            raise _unsupported("stop_sequences", "must be array")
        return seq
    if "stop" in options and options["stop"] is not None:
        value = options["stop"]
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        raise _unsupported("stop", "must be string or array")
    return None


_LIMIT_KEYS = ("max_completion_tokens", "max_tokens", "max_output_tokens")


def _output_limit(normalized: dict[str, Any]) -> int | None:
    """提取输出上限：max_completion_tokens / max_tokens / max_output_tokens。

    §12.6 输出上限行：同一请求同时给出多个上限键时返回 400——即使数值
    相同也不静默择一（调用方无法确认语义）。
    """
    options = _extract_options(normalized)
    present = [key for key in _LIMIT_KEYS if key in options and options[key] is not None]
    # normalized["max_tokens"]（Anthropic 顶级字段）与 options 中的键
    # 同属输出上限语义，纳入冲突检测。
    top_level_present = normalized.get("max_tokens") is not None
    if len(present) + (1 if top_level_present and "max_tokens" not in present else 0) > 1:
        raise _unsupported(
            "output limit fields " + ", ".join(present),
            "conflicting output limit fields in one request",
        )
    if present:
        value = options[present[0]]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise _unsupported(present[0], "must be a positive integer")
        return value
    if top_level_present:
        return int(normalized["max_tokens"])
    return None


def _metadata_to_chat(normalized: dict[str, Any]) -> dict[str, Any]:
    options = _extract_options(normalized)
    result: dict[str, Any] = {}
    identifiers = [
        options[key] for key in ("user", "safety_identifier") if options.get(key) is not None
    ]
    metadata = options.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise _unsupported("metadata", "must be an object")
        extra = set(metadata) - {"user_id"}
        if extra:
            # 额外键不静默丢弃，整请求拒绝（§12.6 metadata 行）。
            raise _unsupported(
                f"metadata key '{min(extra)}'",
                "only metadata.user_id has a cross-protocol equivalent",
            )
        if metadata.get("user_id") is not None:
            identifiers.append(metadata["user_id"])
    if identifiers:
        if not all(isinstance(value, str) for value in identifiers) or len(set(identifiers)) != 1:
            raise _unsupported("user", "invalid or conflicting user identifiers")
        result["user"] = identifiers[0]
    return result


def _metadata_to_anthropic(normalized: dict[str, Any]) -> dict[str, Any]:
    result = _metadata_to_chat(normalized)
    return {"metadata": {"user_id": result["user"]}} if result else {}


# ----------------------------------------------------------------------
# 顶层转换入口
# ----------------------------------------------------------------------


def _reject_cross_protocol_extras(normalized: dict[str, Any], allow: set[str]) -> None:
    """拒绝未在矩阵声明等价的 option 字段（严格模式，不静默忽略）。"""
    options = _extract_options(normalized)
    for key in options:
        if key == "stream_options" and options[key] is not None:
            stream_options = options[key]
            if not isinstance(stream_options, dict):
                raise _unsupported(key, "must be an object")
            check_fields(stream_options, {"include_usage"}, key)
            include_usage = stream_options.get("include_usage")
            if include_usage is not None and not isinstance(include_usage, bool):
                raise _unsupported("include_usage", "must be boolean")
            continue  # 网关输出由调用方协议渲染器生成 usage，不转给另一种协议。
        if key not in allow and options[key] is not None:
            raise _unsupported(key, "no declared cross-protocol equivalent")


def _output_format(normalized: dict[str, Any], *, responses: bool) -> dict[str, Any]:
    options = _extract_options(normalized)
    chat = options.get("response_format")
    text = options.get("text")
    if chat is not None and text is not None:
        raise _unsupported("output format", "conflicting formats")
    if text is not None:
        if not isinstance(text, dict):
            raise _unsupported("text", "must be an object")
        check_fields(text, {"format"}, "text")
        value = text.get("format")
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise _unsupported("text.format", "must be an object")
        kind = value.get("type")
        if kind == "json_schema":
            check_fields(value, {"type", "name", "description", "schema", "strict"}, "text.format")
            chat = {
                "type": kind,
                "json_schema": {key: val for key, val in value.items() if key != "type"},
            }
        else:
            chat = value
    if chat is None:
        return {}
    if not isinstance(chat, dict):
        raise _unsupported("response_format", "must be an object")
    kind = chat.get("type")
    if kind in {"text", "json_object"}:
        check_fields(chat, {"type"}, "response_format")
        result = dict(chat)
    elif kind == "json_schema":
        check_fields(chat, {"type", "json_schema"}, "response_format")
        schema = chat.get("json_schema")
        if not isinstance(schema, dict):
            raise _unsupported("json_schema", "must be an object")
        check_fields(schema, {"name", "description", "schema", "strict"}, "json_schema")
        if (
            not isinstance(schema.get("name"), str)
            or not schema["name"]
            or not isinstance(schema.get("schema"), dict)
        ):
            raise _unsupported("json_schema", "name and schema are required")
        result = {"type": kind, **schema}
    else:
        raise _unsupported("response_format", "unsupported format")
    return {"text": {"format": result}} if responses else {"response_format": chat}


_CHAT_ALLOWED = {
    "safety_identifier",
    "text",
    "response_format",
    "temperature",
    "top_p",
    "stop",
    "stop_sequences",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "user",
    "metadata",
    "parallel_tool_calls",
}
_ANTHROPIC_ALLOWED = {
    "safety_identifier",
    "temperature",
    "top_p",
    "stop",
    "stop_sequences",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "user",
    "metadata",
    "parallel_tool_calls",
    # 以下字段在 to_anthropic_request 内显式拒绝（带明确错误消息），
    # 需要先通过 extras 检查才能命中具体拒绝分支。
    "reasoning",
    "thinking",
    "response_format",
    "text",
    "service_tier",
}
_RESPONSES_ALLOWED = {
    "safety_identifier",
    "temperature",
    "top_p",
    "max_output_tokens",
    "user",
    "metadata",
    "parallel_tool_calls",
    "reasoning",
    "max_tokens",
    "max_completion_tokens",
    "response_format",
    "text",
}


def to_chat_request(normalized: dict[str, Any], real_model: str) -> dict[str, Any]:
    """规范化请求 -> OpenAI Chat Completions 请求体（跨协议严格矩阵）。"""
    _reject_cross_protocol_extras(normalized, _CHAT_ALLOWED)
    body: dict[str, Any] = {
        "model": real_model,
        "messages": _context_to_chat_messages(normalized),
    }
    tools = _tools_to_chat(normalized)
    if tools:
        body["tools"] = tools
    tool_choice = _tool_choice_to_chat(normalized)
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    parallel = parallel_allowed(normalized)
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
    body.update(_sample_params(normalized))
    stop = _stop_to_chat(normalized)
    if stop is not None:
        body["stop"] = stop
    limit = _output_limit(normalized)
    if limit is not None:
        body["max_tokens"] = limit
    body.update(_metadata_to_chat(normalized))
    body.update(_output_format(normalized, responses=False))
    _record_conversion(normalized, "openai_chat")
    return body


def to_anthropic_request(normalized: dict[str, Any], real_model: str) -> dict[str, Any]:
    """规范化请求 -> Anthropic Messages 请求体（跨协议严格矩阵）。"""
    _reject_cross_protocol_extras(normalized, _ANTHROPIC_ALLOWED)
    options = _extract_options(normalized)
    for rejected in ("reasoning", "thinking", "response_format", "text", "service_tier"):
        if options.get(rejected) is not None:
            raise _unsupported(rejected, "no cross-protocol equivalent")
    messages, system = messages_to_anthropic(_context_to_chat_messages(normalized))
    body: dict[str, Any] = {
        "model": real_model,
        "max_tokens": _output_limit(normalized) or 1024,
        "messages": messages,
    }
    if system is not None:
        body["system"] = system
    tools = _tools_to_anthropic(normalized)
    if tools:
        body["tools"] = tools
    tool_choice = _tool_choice_to_anthropic(normalized)
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if parallel_allowed(normalized) is False:
        body.setdefault("tool_choice", {"type": "auto"})["disable_parallel_tool_use"] = True
    body.update(_sample_params(normalized, max_temperature=1))
    stop = _stop_to_anthropic(normalized)
    if stop is not None:
        body["stop_sequences"] = stop
    body.update(_metadata_to_anthropic(normalized))
    _record_conversion(normalized, "anthropic_messages")
    return body


def to_responses_request(normalized: dict[str, Any], real_model: str) -> dict[str, Any]:
    """规范化请求 -> OpenAI Responses 请求体（跨协议严格矩阵）。

    input 用消息项数组：{role, content:[{type:"input_text", text}]}。
    reasoning effort 仅由 LLM 配置决定，不进跨协议矩阵。
    """
    _reject_cross_protocol_extras(normalized, _RESPONSES_ALLOWED)
    options = _extract_options(normalized)
    if options.get("reasoning") is not None:
        raise _unsupported("reasoning", "thinking 由 LLM 配置控制，不支持请求级透传")
    body: dict[str, Any] = {
        "model": real_model,
        "input": messages_to_responses(_context_to_chat_messages(normalized)),
    }
    if normalized.get("store") is False:
        body["store"] = False
    tools = _tools_to_chat(normalized)
    if tools:
        body["tools"] = [{"type": "function", **tool["function"]} for tool in tools]
    choice = _tool_choice_to_chat(normalized)
    if choice is not None:
        body["tool_choice"] = (
            {"type": "function", "name": choice["function"]["name"]}
            if isinstance(choice, dict)
            else choice
        )
    parallel = parallel_allowed(normalized)
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
    body.update(_metadata_to_chat(normalized))
    body.update(_output_format(normalized, responses=True))
    body.update(_sample_params(normalized))
    limit = _output_limit(normalized)
    if limit is not None:
        body["max_output_tokens"] = limit
    _record_conversion(normalized, "openai_responses")
    return body

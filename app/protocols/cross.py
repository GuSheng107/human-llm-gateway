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

from ..domain.errors import DomainError, DomainErrorCode


def _unsupported(field: str, reason: str) -> DomainError:
    return DomainError(
        DomainErrorCode.UNSUPPORTED_PARAMETER,
        f"{field} cannot be forwarded across protocols: {reason}.",
        status_code=400,
    )


# ----------------------------------------------------------------------
# 内容块转换
# ----------------------------------------------------------------------


def _chat_content_to_text(content: Any) -> str:
    """Chat content（字符串或 parts 数组）-> 纯文本。非文本 part 拒绝。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                raise _unsupported("content part", "non-text content block has no equivalent")
        return "\n".join(parts)
    raise _unsupported("content", "unsupported content structure")


def _anthropic_blocks_to_text(blocks: Any) -> str:
    """Anthropic content blocks -> 纯文本；非 text/thinking 之外的块拒绝。"""
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        raise _unsupported("content", "unsupported content structure")
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise _unsupported("content block", "invalid block")
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            continue  # 历史思考内容不进入目标上下文（协议语义均如此）
        else:
            raise _unsupported(f"content block type '{btype}'", "no cross-protocol equivalent")
    return "\n".join(parts)


def _has_cache_control(value: Any) -> bool:
    """检测内容块 / 工具定义中是否携带 cache_control。"""
    if isinstance(value, dict):
        if "cache_control" in value:
            return True
        return any(_has_cache_control(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_cache_control(item) for item in value)
    return False


# ----------------------------------------------------------------------
# 消息（context）转换
# ----------------------------------------------------------------------


def _context_to_chat_messages(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """规范化 context -> Chat messages（文本与角色等价转换）。"""
    context = normalized.get("context") or []
    messages: list[dict[str, Any]] = []
    instructions = normalized.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    system_blocks = normalized.get("system_blocks")
    if system_blocks:
        if _has_cache_control(system_blocks):
            raise _unsupported("system cache_control", "prompt cache is provider-specific")
        messages.append({"role": "system", "content": _anthropic_blocks_to_text(system_blocks)})
    for item in context:
        if not isinstance(item, dict):
            raise _unsupported("context item", "invalid structure")
        if _has_cache_control(item):
            raise _unsupported("cache_control", "prompt cache is provider-specific")
        role = item.get("role")
        if role == "tool":
            # Chat tool role -> 保持（同协议语义）；跨协议到 Anthropic 由
            # _context_to_anthropic_messages 处理，此处仅 Chat 目标使用。
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("tool_call_id", ""),
                    "content": _chat_content_to_text(item.get("content")),
                }
            )
            continue
        if role not in {"user", "assistant", "system", "developer"}:
            raise _unsupported(f"role '{role}'", "no cross-protocol equivalent")
        content = item.get("content")
        messages.append(
            {
                "role": "system" if role == "developer" else role,
                "content": _chat_content_to_text(content)
                if not _looks_like_anthropic_blocks(content)
                else _anthropic_blocks_to_text(content),
            }
        )
        # assistant 历史 tool_calls（Chat 形态）保持
        if role == "assistant" and item.get("tool_calls"):
            messages[-1]["tool_calls"] = item["tool_calls"]
    return messages


def _looks_like_anthropic_blocks(content: Any) -> bool:
    """启发式判断 content 是否为 Anthropic 块数组（type=text 等结构）。"""
    if not isinstance(content, list):
        return False
    return all(
        isinstance(block, dict) and isinstance(block.get("type"), str) and "text" in block
        for block in content
    ) and bool(content)


def _context_to_anthropic_messages(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """规范化 context -> Anthropic messages（user/assistant 文本等价）。"""
    context = normalized.get("context") or []
    messages: list[dict[str, Any]] = []
    for item in context:
        if not isinstance(item, dict):
            raise _unsupported("context item", "invalid structure")
        if _has_cache_control(item):
            raise _unsupported("cache_control", "prompt cache is provider-specific")
        role = item.get("role")
        if role == "tool":
            # Chat tool 结果 -> user 的 tool_result 块（先于 role 白名单判定）
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": item.get("tool_call_id", ""),
                            "content": _chat_content_to_text(item.get("content")),
                        }
                    ],
                }
            )
            continue
        if role not in {"user", "assistant"}:
            raise _unsupported(f"role '{role}'", "anthropic messages only accept user/assistant")
        content = item.get("content")
        if role == "assistant" and item.get("tool_calls"):
            # Chat assistant tool_calls -> tool_use 块
            blocks: list[dict[str, Any]] = []
            text = (
                _anthropic_blocks_to_text(content)
                if _looks_like_anthropic_blocks(content)
                else _chat_content_to_text(content)
            )
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif text:
                blocks.append({"type": "text", "text": text})
            for call in item["tool_calls"]:
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise _unsupported("tool call", "only function calls convert")
                fn = call.get("function") or {}
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args_obj = json_loads(args)
                    except ValueError:
                        args_obj = {}
                else:
                    args_obj = args
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args_obj,
                    }
                )
            messages.append({"role": role, "content": blocks})
            continue
        text = (
            _anthropic_blocks_to_text(content)
            if _looks_like_anthropic_blocks(content)
            else _chat_content_to_text(content)
        )
        messages.append({"role": role, "content": text})
    return messages


def json_loads(value: str) -> Any:
    import json

    return json.loads(value)


# ----------------------------------------------------------------------
# 工具 Schema / 选择 / 并行工具
# ----------------------------------------------------------------------


def _tools_to_chat(normalized: dict[str, Any]) -> list[dict[str, Any]] | None:
    """规范化 tools -> Chat tools（Responses function tool -> function 形态）。"""
    tools = normalized.get("tools")
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise _unsupported("tool", "invalid structure")
        ttype = tool.get("type")
        if ttype == "function" and isinstance(tool.get("function"), dict):
            converted.append(tool)  # 已是 Chat 形态
        elif ttype == "function" and tool.get("name"):
            # Responses 形态：{type: function, name, parameters, ...}
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description"),
                        "parameters": tool.get("parameters"),
                    },
                }
            )
        else:
            raise _unsupported(
                f"tool type '{ttype}'", "managed tools have no cross-protocol equivalent"
            )
    return converted


def _tools_to_anthropic(normalized: dict[str, Any]) -> list[dict[str, Any]] | None:
    """规范化 tools -> Anthropic tools（name/description/input_schema）。"""
    tools = normalized.get("tools")
    if not tools:
        return None
    if _has_cache_control(tools):
        raise _unsupported("tools cache_control", "prompt cache is provider-specific")
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise _unsupported("tool", "invalid structure")
        ttype = tool.get("type")
        if ttype == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            converted.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description"),
                    "input_schema": fn.get("parameters") or {"type": "object"},
                }
            )
        elif ttype == "function" and tool.get("name"):
            converted.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description"),
                    "input_schema": tool.get("parameters") or {"type": "object"},
                }
            )
        else:
            raise _unsupported(
                f"tool type '{ttype}'", "managed tools have no cross-protocol equivalent"
            )
    return converted


def _tool_choice_to_chat(normalized: dict[str, Any]) -> Any:
    choice = normalized.get("tool_choice")
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice in {"auto", "none", "required"}:
            return choice
        raise _unsupported(f"tool_choice '{choice}'", "no chat equivalent")
    if isinstance(choice, dict):
        ctype = choice.get("type")
        if ctype == "function":
            return choice  # Chat 指定函数形态
        if ctype == "tool":
            # Anthropic {type: tool, name} -> Chat 指定函数
            return {
                "type": "function",
                "function": {"name": choice.get("name", "")},
            }
        if ctype in {"auto", "none", "any"}:
            if ctype == "any":
                return "required"
            return ctype
    raise _unsupported("tool_choice", "no equivalent value")


def _tool_choice_to_anthropic(normalized: dict[str, Any]) -> Any:
    choice = normalized.get("tool_choice")
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice == "auto":
            return {"type": "auto"}
        if choice == "none":
            return {"type": "none"}
        if choice == "required":
            return {"type": "any"}
        raise _unsupported(f"tool_choice '{choice}'", "no anthropic equivalent")
    if isinstance(choice, dict):
        ctype = choice.get("type")
        if ctype == "function" and isinstance(choice.get("function"), dict):
            return {"type": "tool", "name": choice["function"].get("name", "")}
        if ctype == "tool":
            return choice
        if ctype in {"auto", "none"}:
            return {"type": ctype}
        if ctype == "any":
            raise _unsupported("tool_choice any", "origin is anthropic-only form")
    raise _unsupported("tool_choice", "no equivalent value")


# ----------------------------------------------------------------------
# 采样 / 停止 / 输出上限 / metadata
# ----------------------------------------------------------------------


def _extract_options(normalized: dict[str, Any]) -> dict[str, Any]:
    return normalized.get("options") or {}


def _sample_params(normalized: dict[str, Any]) -> dict[str, Any]:
    options = _extract_options(normalized)
    result: dict[str, Any] = {}
    for key in ("temperature", "top_p"):
        if key in options and options[key] is not None:
            value = options[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise _unsupported(key, "must be numeric")
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
    if "user" in options and options["user"] is not None:
        result["user"] = options["user"]
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
        result["user"] = metadata.get("user_id")
    return result


def _metadata_to_anthropic(normalized: dict[str, Any]) -> dict[str, Any]:
    options = _extract_options(normalized)
    result: dict[str, Any] = {}
    if "user" in options and options["user"] is not None:
        result["metadata"] = {"user_id": options["user"]}
    metadata = options.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise _unsupported("metadata", "must be an object")
        extra = set(metadata) - {"user_id"}
        if extra:
            raise _unsupported(
                f"metadata key '{min(extra)}'",
                "only metadata.user_id has a cross-protocol equivalent",
            )
        result["metadata"] = {"user_id": metadata.get("user_id")}
    return result


# ----------------------------------------------------------------------
# 顶层转换入口
# ----------------------------------------------------------------------


def _reject_cross_protocol_extras(normalized: dict[str, Any], allow: set[str]) -> None:
    """拒绝未在矩阵声明等价的 option 字段（严格模式，不静默忽略）。"""
    options = _extract_options(normalized)
    for key in options:
        if key not in allow:
            raise _unsupported(key, "no declared cross-protocol equivalent")


_CHAT_ALLOWED = {
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
    "temperature",
    "top_p",
    "stop",
    "max_output_tokens",
    "user",
    "metadata",
    "parallel_tool_calls",
    "reasoning",
}


def to_chat_request(normalized: dict[str, Any], real_model: str) -> dict[str, Any]:
    """规范化请求 -> OpenAI Chat Completions 请求体（跨协议严格矩阵）。"""
    _reject_cross_protocol_extras(normalized, _CHAT_ALLOWED)
    if normalized.get("text") or _extract_options(normalized).get("text"):
        # Responses text.format（结构化输出）-> Chat response_format 可转换，
        # 但 M7-D 仅处理基础字段；声明为后续开放。
        raise _unsupported("text.format", "structured output conversion pending")
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
    options = _extract_options(normalized)
    if "parallel_tool_calls" in options and options["parallel_tool_calls"] is not None:
        body["parallel_tool_calls"] = bool(options["parallel_tool_calls"])
    body.update(_sample_params(normalized))
    stop = _stop_to_chat(normalized)
    if stop is not None:
        body["stop"] = stop
    limit = _output_limit(normalized)
    if limit is not None:
        body["max_tokens"] = limit
    body.update(_metadata_to_chat(normalized))
    return body


def to_anthropic_request(normalized: dict[str, Any], real_model: str) -> dict[str, Any]:
    """规范化请求 -> Anthropic Messages 请求体（跨协议严格矩阵）。"""
    _reject_cross_protocol_extras(normalized, _ANTHROPIC_ALLOWED)
    options = _extract_options(normalized)
    for rejected in ("reasoning", "thinking", "response_format", "text", "service_tier"):
        if options.get(rejected) is not None:
            raise _unsupported(rejected, "no cross-protocol equivalent")
    body: dict[str, Any] = {
        "model": real_model,
        "max_tokens": _output_limit(normalized) or 1024,
        "messages": _context_to_anthropic_messages(normalized),
    }
    instructions = normalized.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        body["system"] = instructions
    system_blocks = normalized.get("system_blocks")
    if system_blocks:
        body["system"] = _anthropic_blocks_to_text(system_blocks)
    tools = _tools_to_anthropic(normalized)
    if tools:
        body["tools"] = tools
    tool_choice = _tool_choice_to_anthropic(normalized)
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if (
        "parallel_tool_calls" in options
        and options["parallel_tool_calls"] is not None
        and not bool(options["parallel_tool_calls"])
    ):
        # Chat parallel_tool_calls=false -> Anthropic disable_parallel_tool_use=true
        # （布尔取反）；true 对应 Anthropic 默认允许，无需附加字段。
        existing = body.get("tool_choice")
        if isinstance(existing, dict):
            existing["disable_parallel_tool_use"] = True
        else:
            body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    body.update(_sample_params(normalized))
    stop = _stop_to_anthropic(normalized)
    if stop is not None:
        body["stop_sequences"] = stop
    body.update(_metadata_to_anthropic(normalized))
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
    input_items: list[dict[str, Any]] = []
    instructions = normalized.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        input_items.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": f"[system] {instructions}"}],
            }
        )
    for item in _context_to_chat_messages(normalized):
        role = item.get("role")
        if role not in {"user", "assistant", "system"}:
            continue
        text = item.get("content") if isinstance(item.get("content"), str) else str(item.get("content") or "")
        input_items.append(
            {"role": role, "content": [{"type": "input_text", "text": text}]}
        )
    body: dict[str, Any] = {"model": real_model, "input": input_items}
    tools = _tools_to_chat(normalized)
    if tools:
        body["tools"] = tools
    body.update(_sample_params(normalized))
    limit = _output_limit(normalized)
    if limit is not None:
        body["max_output_tokens"] = limit
    return body

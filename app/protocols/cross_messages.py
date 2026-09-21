"""跨协议文本和工具历史转换；原始请求不修改，不支持的块显式拒绝。"""

from __future__ import annotations

import json
from typing import Any

from ..core.logging import log_event
from ..domain.errors import DomainError, DomainErrorCode


def unsupported(field: str, reason: str) -> DomainError:
    log_event(
        "warning",
        "llm.protocol.field_rejected",
        "跨协议字段无法等价转换",
        field=field,
        action="reject",
        result="unsupported_parameter",
    )
    return DomainError(
        DomainErrorCode.UNSUPPORTED_PARAMETER,
        f"{field} cannot be forwarded across protocols: {reason}.",
        status_code=400,
    )


def check_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    for key, item in value.items():
        if key not in allowed and item is not None:
            raise unsupported(f"{label}.{key}", "no declared equivalent")


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise unsupported(label, "must be a non-empty string")
    return value


def arguments_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise unsupported("arguments", "must be valid JSON object") from None
    if not isinstance(value, dict):
        raise unsupported("arguments", "must be a JSON object")
    return value


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise unsupported("content", "must be text or text blocks")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {
            "text",
            "input_text",
            "output_text",
        }:
            raise unsupported("content block", "only text has an equivalent")
        check_fields(block, {"type", "text", "annotations"}, "content")
        if block.get("annotations"):
            raise unsupported("annotations", "provider annotations have no equivalent")
        if not isinstance(block.get("text"), str):
            raise unsupported("text", "must be a string")
        parts.append(block["text"])
    return "\n".join(parts)


def _call(call_id: Any, name: Any, arguments: Any) -> dict[str, Any]:
    return {
        "id": required_string(call_id, "call_id"),
        "type": "function",
        "function": {
            "name": required_string(name, "name"),
            "arguments": json.dumps(arguments_object(arguments), ensure_ascii=False),
        },
    }


def context_to_chat_messages(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """以 Chat 文本/工具历史作为转换中间结构，保留调用关联与顺序。"""
    messages: list[dict[str, Any]] = []
    instructions = normalized.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str):
            raise unsupported("instructions", "must be a string")
        if instructions:
            messages.append({"role": "system", "content": instructions})
    if normalized.get("system_blocks"):
        messages.append({"role": "system", "content": text_content(normalized["system_blocks"])})
    for item in normalized.get("context") or []:
        if not isinstance(item, dict):
            raise unsupported("context", "invalid item")
        kind = item.get("type")
        if kind == "function_call":
            check_fields(
                item, {"type", "id", "call_id", "name", "arguments", "status"}, "function_call"
            )
            if item.get("status") not in {None, "completed"}:
                raise unsupported("function_call.status", "incomplete history")
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _call(item.get("call_id"), item.get("name"), item.get("arguments"))
                    ],
                }
            )
            continue
        if kind == "function_call_output":
            check_fields(
                item, {"type", "id", "call_id", "output", "status"}, "function_call_output"
            )
            if item.get("status") not in {None, "completed"}:
                raise unsupported("function_call_output.status", "incomplete history")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": required_string(item.get("call_id"), "call_id"),
                    "content": text_content(item.get("output")),
                }
            )
            continue
        if kind not in {None, "message"}:
            raise unsupported("context.type", "no equivalent history item")
        check_fields(
            item,
            {"type", "id", "status", "role", "content", "tool_calls", "tool_call_id"},
            "message",
        )
        if item.get("status") not in {None, "completed"}:
            raise unsupported("message.status", "incomplete history")
        role = item.get("role")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise unsupported("role", "unsupported message role")
        content = item.get("content")
        if role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": required_string(item.get("tool_call_id"), "tool_call_id"),
                    "content": text_content(content),
                }
            )
            continue
        # Anthropic 的工具块不能作为文本丢弃。user 的工具结果先后顺序原样展开。
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
            for block in content
        ):
            if item.get("tool_calls"):
                raise unsupported("tool_calls", "mixed protocol tool representations")
            for block in content:
                if not isinstance(block, dict):
                    raise unsupported("content", "invalid block")
                if block.get("type") == "tool_use":
                    if role != "assistant":
                        raise unsupported("tool_use", "requires assistant role")
                    check_fields(block, {"type", "id", "name", "input"}, "tool_use")
                    messages.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                _call(block.get("id"), block.get("name"), block.get("input"))
                            ],
                        }
                    )
                elif block.get("type") == "tool_result":
                    if role != "user":
                        raise unsupported("tool_result", "requires user role")
                    check_fields(
                        block, {"type", "tool_use_id", "content", "is_error"}, "tool_result"
                    )
                    if block.get("is_error") not in {None, False}:
                        raise unsupported("tool_result.is_error", "no equivalent error marker")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": required_string(
                                block.get("tool_use_id"), "tool_use_id"
                            ),
                            "content": text_content(block.get("content", "")),
                        }
                    )
                else:
                    messages.append({"role": role, "content": text_content([block])})
            continue
        message: dict[str, Any] = {
            "role": "system" if role == "developer" else role,
            "content": None
            if content is None and role == "assistant" and item.get("tool_calls")
            else text_content(content),
        }
        if item.get("tool_calls"):
            if role != "assistant" or not isinstance(item["tool_calls"], list):
                raise unsupported("tool_calls", "requires assistant call array")
            calls = []
            for call in item["tool_calls"]:
                if (
                    not isinstance(call, dict)
                    or call.get("type") != "function"
                    or not isinstance(call.get("function"), dict)
                ):
                    raise unsupported("tool_call", "only function calls convert")
                check_fields(call, {"id", "type", "function"}, "tool_call")
                fn = call["function"]
                check_fields(fn, {"name", "arguments"}, "function")
                calls.append(_call(call.get("id"), fn.get("name"), fn.get("arguments")))
            message["tool_calls"] = calls
        messages.append(message)
    merged: list[dict[str, Any]] = []
    for message in messages:
        if merged and message["role"] == "assistant" and merged[-1]["role"] == "assistant":
            previous = merged[-1]
            if message.get("content"):
                previous["content"] = "\n".join(
                    filter(None, [previous.get("content"), message["content"]])
                )
            if message.get("tool_calls"):
                previous.setdefault("tool_calls", []).extend(message["tool_calls"])
        else:
            merged.append(message)
    return merged


def messages_to_responses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
            continue
        if message.get("content") is not None:
            items.append(
                {"type": "message", "role": message["role"], "content": message["content"]}
            )
        for call in message.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"], **call["function"]})
    return items


def messages_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    result: list[dict[str, Any]] = []
    system: list[str] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            if result:
                raise unsupported("system", "system instructions after dialogue cannot be promoted")
            system.append(message["content"])
            continue
        blocks: list[dict[str, Any]] = []
        if role == "tool":
            role = "user"
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
            )
        else:
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": arguments_object(call["function"]["arguments"]),
                    }
                )
        # Anthropic 相邻同角色消息合并为块，工具结果顺序保持。
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": blocks})
    for message in result:
        blocks = message["content"]
        if len(blocks) == 1 and blocks[0]["type"] == "text":
            message["content"] = blocks[0]["text"]
    return result, "\n\n".join(system) if system else None

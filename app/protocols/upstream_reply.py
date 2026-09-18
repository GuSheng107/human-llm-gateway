"""三协议上游非流式响应归一；损坏工具参数不能被替换成空对象。"""

from __future__ import annotations

import json
from typing import Any

from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft


def _invalid() -> DomainError:
    return DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游响应结构无效", status_code=502)


def _tool(call_id: Any, name: Any, arguments: Any) -> dict[str, Any]:
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise _invalid()
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise _invalid() from None
    if not isinstance(arguments, dict):
        raise _invalid()
    return {"id": call_id, "name": name, "arguments": arguments}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid()
    return value


def parse_chat_response(payload: dict[str, Any]) -> ReplyDraft:
    choices = payload.get("choices")
    if (
        payload.get("error")
        or not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], dict)
    ):
        raise _invalid()
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise _invalid()
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        raise _invalid()
    tools = []
    for call in calls:
        if (
            not isinstance(call, dict)
            or call.get("type") != "function"
            or not isinstance(call.get("function"), dict)
        ):
            raise _invalid()
        fn = call["function"]
        tools.append(_tool(call.get("id"), fn.get("name"), fn.get("arguments")))
    return ReplyDraft(
        reasoning=_text(message.get("reasoning_content")) or None,
        tool_calls=tools,
        final_text=_text(message.get("content")) or None,
    )


def parse_responses_response(payload: dict[str, Any]) -> ReplyDraft:
    if payload.get("error") or payload.get("status") not in {None, "completed"}:
        raise _invalid()
    output = payload.get("output")
    if not isinstance(output, list):
        raise _invalid()
    texts: list[str] = []
    reasoning: list[str] = []
    calls = []
    for item in output:
        if not isinstance(item, dict):
            raise _invalid()
        kind = item.get("type")
        if kind == "function_call":
            if item.get("status") not in {None, "completed"}:
                raise _invalid()
            calls.append(_tool(item.get("call_id"), item.get("name"), item.get("arguments")))
        elif kind in {"message", "reasoning"}:
            parts = item.get("content" if kind == "message" else "summary") or []
            if not isinstance(parts, list):
                raise _invalid()
            for part in parts:
                if not isinstance(part, dict):
                    raise _invalid()
                if kind == "message" and part.get("type") == "output_text":
                    texts.append(_text(part.get("text")))
                elif kind == "message" and part.get("type") == "refusal":
                    texts.append(_text(part.get("refusal")))
                elif kind == "reasoning" and part.get("type") == "summary_text":
                    reasoning.append(_text(part.get("text")))
                else:
                    raise _invalid()
        else:
            raise _invalid()
    return ReplyDraft(
        reasoning="".join(reasoning) or None, tool_calls=calls, final_text="".join(texts) or None
    )


def parse_anthropic_response(payload: dict[str, Any]) -> ReplyDraft:
    content = payload.get("content")
    if payload.get("error") or not isinstance(content, list):
        raise _invalid()
    texts: list[str] = []
    reasoning: list[str] = []
    calls = []
    for block in content:
        if not isinstance(block, dict):
            raise _invalid()
        kind = block.get("type")
        if kind == "text":
            texts.append(_text(block.get("text")))
        elif kind == "thinking":
            reasoning.append(_text(block.get("thinking")))
        elif kind == "tool_use":
            calls.append(_tool(block.get("id"), block.get("name"), block.get("input")))
        elif kind != "redacted_thinking":
            raise _invalid()
    return ReplyDraft(
        reasoning="\n".join(reasoning) or None,
        tool_calls=calls,
        final_text="\n".join(texts) or None,
    )

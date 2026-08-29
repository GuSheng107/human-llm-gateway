"""OpenAI Responses 协议（docs/API_CONTRACT.md §14）。

`previous_response_id` 为网关控制字段（§12.5）：由服务层解析、校验并
把历史上下文等价展开到 normalized context；原始 payload 完整保存。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from sse_starlette import ServerSentEvent

from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from .normalized import decode_object, reject_unsupported_field, require_non_empty_str

_CONSUMED_FIELDS = {
    "model",
    "input",
    "instructions",
    "stream",
    "previous_response_id",
    "background",
    "conversation",
    "store",
}
_OPTION_ALLOWLIST = (
    "temperature",
    "top_p",
    "max_output_tokens",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "text",
    "metadata",
    "reasoning",
    "service_tier",
    "safety_identifier",
)


class ResponsesRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        for field in ("background", "conversation", "store"):
            reject_unsupported_field(payload, field)
        self.model = require_non_empty_str(payload, "model")
        input_value = payload.get("input")
        if input_value is None or (isinstance(input_value, (list, str)) and not len(input_value)):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                "input 必须是非空字符串或输入项数组",
                status_code=400,
            )
        if not isinstance(input_value, (str, list)):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST, "input 必须是字符串或输入项数组", status_code=400
            )
        self.input = input_value
        self.instructions = payload.get("instructions")
        if self.instructions is not None and not isinstance(self.instructions, str):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST, "instructions 必须是字符串", status_code=400
            )
        previous = payload.get("previous_response_id")
        if previous is not None and not isinstance(previous, str):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                "previous_response_id 必须是字符串",
                status_code=400,
            )
        self.previous_response_id = previous or None
        self.stream = bool(payload.get("stream", False))
        self.tools = payload.get("tools")
        self.options = {key: value for key, value in payload.items() if key not in _CONSUMED_FIELDS}
        self.raw = payload

    def base_context_items(self) -> list[Any]:
        """本条请求的原始输入项（历史上下文由服务层在链展开时追加在头部）。"""
        if isinstance(self.input, str):
            return [{"type": "message", "role": "user", "content": self.input}]
        return list(self.input)

    def normalized_request(self, context: list[Any]) -> dict[str, Any]:
        return {
            "context": context,
            "instructions": self.instructions,
            "tools": self.tools,
            "tool_choice": self.options.get("tool_choice"),
            "options": {
                key: value for key, value in self.options.items() if key in _OPTION_ALLOWLIST
            },
            "input": self.input,
            "stream": self.stream,
        }


def parse_request(raw: bytes) -> ResponsesRequest:
    return ResponsesRequest(decode_object(raw))


def reply_output_items(draft: ReplyDraft) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """ReplyDraft -> Responses output items；返回 (items, message_item)。

    message_item 同时用于非流式响应的简易拼装；reasoning 与函数调用按
    输出项顺序在前。
    """
    items: list[dict[str, Any]] = []
    message_item: dict[str, Any] | None = None
    if draft.reasoning:
        items.append(
            {
                "id": "rs_reasoning",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": draft.reasoning}],
            }
        )
    for call in draft.tool_calls:
        items.append(
            {
                "type": "function_call",
                "id": f"fc_{call.id}",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            }
        )
    message_item = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": draft.final_text or ""}],
    }
    items.append(message_item)
    return items, message_item


def response_object(
    model: str, response_id: str, draft: ReplyDraft, *, status: str = "completed"
) -> dict[str, Any]:
    items, _message = reply_output_items(draft)
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": items if status == "completed" else [],
    }


def render_response(model: str, response_id: str, draft: ReplyDraft) -> dict[str, Any]:
    return response_object(model, response_id, draft)


def stream_events(model: str, response_id: str, draft: ReplyDraft) -> Iterator[ServerSentEvent]:
    """人工伪流式：response.created -> in_progress -> 输出项 added/done -> completed。"""
    items, _message = reply_output_items(draft)
    response = response_object(model, response_id, draft, status="in_progress")

    def frame(event: str, data: dict[str, Any]) -> ServerSentEvent:
        data = dict(data)
        data.setdefault("type", event)
        return ServerSentEvent(event=event, data=json.dumps(data, ensure_ascii=False))

    yield frame("response.created", {"response": response})
    yield frame("response.in_progress", {"response": response})
    for index, item in enumerate(items):
        yield frame(
            "response.output_item.added",
            {"output_index": index, "item": item},
        )
        yield frame(
            "response.output_item.done",
            {"output_index": index, "item": item},
        )
    final = response_object(model, response_id, draft, status="completed")
    yield frame("response.completed", {"response": final})


def stream_error_event(
    response_id: str, model: str, message: str = "Internal error"
) -> ServerSentEvent:
    """SSE 头已发出后的失败终态（§16.4）：沿用同一 resp ID，随后结束流。"""
    response = {
        "id": response_id,
        "object": "response",
        "status": "failed",
        "model": model,
        "error": {"code": "server_error", "message": message},
    }
    return ServerSentEvent(
        event="response.failed",
        data=json.dumps({"type": "response.failed", "response": response}, ensure_ascii=False),
    )

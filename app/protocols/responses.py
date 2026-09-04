"""OpenAI Responses 协议（docs/API_CONTRACT.md §14）。

`previous_response_id` 为网关控制字段（§12.5）：由服务层解析、校验并
把历史上下文等价展开到 normalized context；原始 payload 完整保存。

响应补齐官方 SDK 必需字段：usage、消息 ID、annotations、sequence_number
与流式 content part / delta 事件。
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
        for field in ("background", "conversation"):
            reject_unsupported_field(payload, field)
        store = payload.get("store")
        if store is not None and not isinstance(store, bool):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                "store 必须是布尔值",
                status_code=400,
            )
        if store is True:
            reject_unsupported_field(payload, "store")
        self.store: bool | None = store
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
            "store": self.store,
        }


def parse_request(raw: bytes) -> ResponsesRequest:
    return ResponsesRequest(decode_object(raw))


def _annotation_blocks() -> list[dict[str, Any]]:
    """Responses message 内容块的标准 annotations 数组（空集合）。"""
    return []


def _message_item(item_id: str, text: str, *, status: str = "completed") -> dict[str, Any]:
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": status,
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": _annotation_blocks(),
            }
        ],
    }


def reply_output_items(draft: ReplyDraft) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """ReplyDraft -> Responses output items；返回 (items, message_item)。

    message_item 同时用于非流式响应的简易拼装；reasoning 与函数调用按
    输出项顺序在前。每项均带稳定 ID；message 内容块带 annotations。
    """
    items: list[dict[str, Any]] = []
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
                "status": "completed",
            }
        )
    message_item = _message_item("msg_reply", draft.final_text or "")
    items.append(message_item)
    return items, message_item


def response_object(
    model: str,
    response_id: str,
    draft: ReplyDraft,
    *,
    status: str = "completed",
    usage: dict[str, int] | None = None,
    sequence_start: int = 0,
) -> dict[str, Any]:
    items, _message = reply_output_items(draft)
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": items if status == "completed" else [],
        "usage": usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "sequence_number_start": sequence_start,
    }


def render_response(
    model: str,
    response_id: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    body = response_object(model, response_id, draft, usage=usage)
    # sequence_number_start 是网关内部字段，不进最终响应体。
    body.pop("sequence_number_start", None)
    return body


def stream_events(
    model: str,
    response_id: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
) -> Iterator[ServerSentEvent]:
    """人工伪流式：完整 Responses 事件序列（官方 SDK 可直接解析）。

    事件链：response.created -> response.in_progress ->
    每项 output_item.added -> （内容 part added -> delta -> done）->
    output_item.done -> response.completed（带 usage）。
    sequence_number 逐事件递增，completed 事件的 response.sequence_number
    为末值 + 1。
    """
    sequence = 0

    def next_seq() -> int:
        nonlocal sequence
        current = sequence
        sequence += 1
        return current

    def frame(event: str, data: dict[str, Any]) -> ServerSentEvent:
        data = dict(data)
        data.setdefault("type", event)
        data.setdefault("sequence_number", next_seq())
        return ServerSentEvent(event=event, data=json.dumps(data, ensure_ascii=False))

    items, _message = reply_output_items(draft)
    usage_body = usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    in_progress_body = response_object(
        model, response_id, draft, status="in_progress", usage=usage_body
    )
    in_progress_body.pop("sequence_number_start", None)
    yield frame("response.created", {"response": {**in_progress_body, "output": []}})
    yield frame("response.in_progress", {"response": in_progress_body})

    for index, item in enumerate(items):
        yield frame(
            "response.output_item.added",
            {"output_index": index, "item": item},
        )
        if item.get("type") == "message":
            content_blocks = item.get("content") or []
            for part_index, block in enumerate(content_blocks):
                text = str(block.get("text") or "")
                yield frame(
                    "response.content_part.added",
                    {
                        "item_id": item.get("id"),
                        "output_index": index,
                        "content_index": part_index,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    },
                )
                for char_index in range(0, max(len(text), 1), 200):
                    yield frame(
                        "response.output_text.delta",
                        {
                            "item_id": item.get("id"),
                            "output_index": index,
                            "content_index": part_index,
                            "delta": text[char_index : char_index + 200],
                        },
                    )
                yield frame(
                    "response.output_text.done",
                    {
                        "item_id": item.get("id"),
                        "output_index": index,
                        "content_index": part_index,
                        "text": text,
                    },
                )
                yield frame(
                    "response.content_part.done",
                    {
                        "item_id": item.get("id"),
                        "output_index": index,
                        "content_index": part_index,
                        "part": {**block, "annotations": block.get("annotations") or []},
                    },
                )
        elif item.get("type") == "function_call":
            arguments = str(item.get("arguments") or "{}")
            for arg_index in range(0, max(len(arguments), 1), 200):
                yield frame(
                    "response.function_call_arguments.delta",
                    {
                        "item_id": item.get("id"),
                        "output_index": index,
                        "delta": arguments[arg_index : arg_index + 200],
                    },
                )
            yield frame(
                "response.function_call_arguments.done",
                {
                    "item_id": item.get("id"),
                    "output_index": index,
                    "arguments": arguments,
                },
            )
        yield frame(
            "response.output_item.done",
            {"output_index": index, "item": item},
        )

    final = response_object(model, response_id, draft, usage=usage_body)
    final.pop("sequence_number_start", None)
    final["sequence_number"] = sequence + 1
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

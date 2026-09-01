"""OpenAI Chat Completions 协议（docs/API_CONTRACT.md §13）。

请求诊断保持透传：除契约声明的网关控制字段外，全部字段进入
normalized.options 并完整保存在原始 payload；人工回复渲染遵循
`reasoning_content` / `message.tool_calls` / finish_reason + [DONE]。
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterator
from typing import Any

from sse_starlette import ServerSentEvent

from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from .normalized import decode_object, reject_unsupported_field, require_non_empty_str

# 契约声明的网关消费字段：不进入透传 options
_CONSUMED_FIELDS = {"model", "messages", "stream", "store"}
# 响应中禁止回显的供应商专有推理控制字段（跨协议等价在 M7 处理）
_OPTION_ALLOWLIST = (
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "stop",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
    "user",
    "metadata",
)


class ChatCompletionsRequest:
    """解析后的规范化视图；原始 payload 另行完整落库。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        reject_unsupported_field(payload, "store")
        self.model = require_non_empty_str(payload, "model")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST, "messages 必须是非空数组", status_code=400
            )
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("role"), str):
                raise DomainError(
                    DomainErrorCode.INVALID_REQUEST,
                    "messages 中存在非法条目",
                    status_code=400,
                )
        self.messages = messages
        self.stream = bool(payload.get("stream", False))
        stream_options = payload.get("stream_options")
        if stream_options is not None and not isinstance(stream_options, dict):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST, "stream_options 必须是对象", status_code=400
            )
        self.stream_options = stream_options
        self.tools = payload.get("tools")
        self.tool_choice = payload.get("tool_choice")
        self.options = {key: value for key, value in payload.items() if key not in _CONSUMED_FIELDS}
        self.raw = payload

    def normalized_request(self) -> dict[str, Any]:
        return {
            "context": list(self.messages),
            "instructions": None,
            "tools": self.tools,
            "tool_choice": self.tool_choice,
            "options": {
                key: value for key, value in self.options.items() if key in _OPTION_ALLOWLIST
            },
            "messages": self.messages,
            "stream": self.stream,
        }


def parse_request(raw: bytes) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(decode_object(raw))


def _chat_id() -> str:
    return "chatcmpl-" + secrets.token_hex(12)


def _finish_reason(draft: ReplyDraft) -> str:
    return "tool_calls" if draft.tool_calls else "stop"


def render_response(
    model: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """非流式 chat.completion 响应；model 始终为请求的 Fake Model。"""
    tool_calls = [
        {
            "id": call.id or f"call_{secrets.token_hex(12)}",
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        for call in draft.tool_calls
    ]
    message: dict[str, Any] = {"role": "assistant", "content": draft.final_text}
    if draft.reasoning:
        message["reasoning_content"] = draft.reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": _chat_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _finish_reason(draft),
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _chunk(
    chat_id: str,
    model: str,
    created: int,
    delta: dict[str, Any],
    finish: str | None = None,
) -> ServerSentEvent:
    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return ServerSentEvent(data=json.dumps(payload, ensure_ascii=False))


def stream_frames(
    model: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
    include_usage: bool = False,
) -> Iterator[ServerSentEvent]:
    """人工伪流式：角色 -> reasoning -> tool calls -> 正文 -> 结束块 + [DONE]。

    同一次流式响应保持稳定的 id 与 created；``include_usage`` 时在 finish
    帧后追加带 usage 的空 choices 帧（OpenAI stream_options 语义）。
    """
    chat_id = _chat_id()
    created = int(time.time())
    yield _chunk(chat_id, model, created, {"role": "assistant"})
    if draft.reasoning:
        for index in range(0, len(draft.reasoning), 200):
            yield _chunk(
                chat_id, model, created, {"reasoning_content": draft.reasoning[index : index + 200]}
            )
    for position, call in enumerate(draft.tool_calls):
        call_id = call.id or f"call_{secrets.token_hex(12)}"
        yield _chunk(
            chat_id,
            model,
            created,
            {
                "tool_calls": [
                    {
                        "index": position,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": ""},
                    }
                ]
            },
        )
        arguments = json.dumps(call.arguments, ensure_ascii=False)
        for index in range(0, max(len(arguments), 1), 200):
            yield _chunk(
                chat_id,
                model,
                created,
                {
                    "tool_calls": [
                        {
                            "index": position,
                            "function": {"arguments": arguments[index : index + 200]},
                        }
                    ]
                },
            )
    if draft.final_text:
        for index in range(0, len(draft.final_text), 200):
            yield _chunk(
                chat_id, model, created, {"content": draft.final_text[index : index + 200]}
            )
    yield _chunk(chat_id, model, created, {}, finish=_finish_reason(draft))
    if include_usage and usage:
        payload = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }
        yield ServerSentEvent(data=json.dumps(payload, ensure_ascii=False))
    yield ServerSentEvent(data="[DONE]")


def stream_error_frame(message: str = "The server had an error.") -> ServerSentEvent:
    """SSE 头已发出后的流内错误帧（§16.4）：不伪造正常完成、不发送 [DONE]。

    帧格式由锁定版本 openai SDK 契约测试固化（tests/test_m6_chat_stream_sdk.py）。
    """
    payload = {"error": {"message": message, "type": "server_error", "code": "internal_error"}}
    return ServerSentEvent(data=json.dumps(payload, ensure_ascii=False))

"""Anthropic Messages 协议（docs/API_CONTRACT.md §15）。

鉴权接受官方 `x-api-key` 头；同协议专有块（如 cache_control）保留在
原始 payload 与规范化投影中，人工流程不执行其语义。

thinking 处理：Anthropic 的 thinking block 需要真实模型返回的加密签名，
人工内容无法生成有效 signature —— 人工路径不返回 thinking block（内部
工作台仍可见 reasoning）；正文与 tool_use 照常输出。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from typing import Any

from sse_starlette import ServerSentEvent

from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from .normalized import decode_object, require_non_empty_str

_CONSUMED_FIELDS = {"model", "messages", "system", "stream", "max_tokens"}
_OPTION_ALLOWLIST = (
    "temperature",
    "top_p",
    "stop_sequences",
    "tools",
    "tool_choice",
    "metadata",
    "thinking",
)


class AnthropicRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
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
        max_tokens = payload.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                "max_tokens 必须是正整数",
                status_code=400,
            )
        self.max_tokens = max_tokens
        system = payload.get("system")
        if system is not None and not isinstance(system, (str, list)):
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST, "system 必须是字符串或内容块数组", status_code=400
            )
        self.system = system
        self.stream = bool(payload.get("stream", False))
        self.tools = payload.get("tools")
        self.options = {key: value for key, value in payload.items() if key not in _CONSUMED_FIELDS}
        self.raw = payload

    def normalized_request(self) -> dict[str, Any]:
        return {
            "context": list(self.messages),
            "instructions": self.system if isinstance(self.system, str) else None,
            "system_blocks": self.system if isinstance(self.system, list) else None,
            "tools": self.tools,
            "tool_choice": self.options.get("tool_choice"),
            "options": {
                key: value for key, value in self.options.items() if key in _OPTION_ALLOWLIST
            },
            "max_tokens": self.max_tokens,
            "messages": self.messages,
            "stream": self.stream,
        }


def parse_request(raw: bytes) -> AnthropicRequest:
    return AnthropicRequest(decode_object(raw))


def content_blocks(draft: ReplyDraft) -> list[dict[str, Any]]:
    """ReplyDraft -> 内容块：tool_use / text（人工路径不返回 thinking block）。

    Anthropic thinking block 需要真实模型签名（signature），人工内容无法
    生成有效签名；reasoning 仅在内部工作台展示（§15.2 决策）。
    """
    blocks: list[dict[str, Any]] = []
    for call in draft.tool_calls:
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id or f"toolu_{secrets.token_hex(12)}",
                "name": call.name,
                "input": call.arguments,
            }
        )
    blocks.append({"type": "text", "text": draft.final_text or ""})
    return blocks


def render_response(
    model: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    blocks = content_blocks(draft)
    input_tokens = (usage or {}).get("input_tokens", 0)
    output_tokens = (usage or {}).get("output_tokens", 0)
    return {
        "id": f"msg_{secrets.token_hex(12)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": "tool_use" if draft.tool_calls else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def stream_events(
    model: str,
    draft: ReplyDraft,
    *,
    usage: dict[str, int] | None = None,
) -> Iterator[ServerSentEvent]:
    """人工伪流式（§15.3）：message_start -> 块序列 -> message_delta -> message_stop。"""
    blocks = content_blocks(draft)
    input_tokens = (usage or {}).get("input_tokens", 0)
    output_tokens = (usage or {}).get("output_tokens", 0)
    message = {
        "id": f"msg_{secrets.token_hex(12)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }

    def frame(event: str, data: dict[str, Any]) -> ServerSentEvent:
        data = dict(data)
        data.setdefault("type", event)
        return ServerSentEvent(event=event, data=json.dumps(data, ensure_ascii=False))

    yield frame("message_start", {"message": message})
    for index, block in enumerate(blocks):
        if block["type"] == "tool_use":
            start_block = {**block, "input": {}}
        else:
            start_block = {**block, "text": ""}
        yield frame("content_block_start", {"index": index, "content_block": start_block})
        if block["type"] == "tool_use":
            partial = json.dumps(block["input"], ensure_ascii=False)
            for i in range(0, max(len(partial), 1), 200):
                yield frame(
                    "content_block_delta",
                    {
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": partial[i : i + 200],
                        },
                    },
                )
        else:
            text = draft.final_text or ""
            for i in range(0, len(text), 200):
                yield frame(
                    "content_block_delta",
                    {
                        "index": index,
                        "delta": {"type": "text_delta", "text": text[i : i + 200]},
                    },
                )
        yield frame("content_block_stop", {"index": index})
    yield frame(
        "message_delta",
        {
            "delta": {
                "stop_reason": "tool_use" if draft.tool_calls else "end_turn",
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield frame("message_stop", {})


def stream_error_event(message: str = "Internal error") -> ServerSentEvent:
    """SSE 头已发出后的失败帧（§16.4）：generic api_error，随后结束流。"""
    payload = {"type": "error", "error": {"type": "api_error", "message": message}}
    return ServerSentEvent(event="error", data=json.dumps(payload, ensure_ascii=False))

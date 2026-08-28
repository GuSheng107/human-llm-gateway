from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..config import Settings
from ..dsl import ParsedEvent
from ..enums import EventKind
from ..models import RequestTask
from .common import output_tokens, pseudo_streamer, sse, thinking_signature, tool_call_id


def anthropic_json(task: RequestTask, events: list[ParsedEvent]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    tool_index = 0
    for index, event in enumerate(events):
        if event.kind is EventKind.REASONING:
            content.append(
                {
                    "type": "thinking",
                    "thinking": event.content,
                    "signature": thinking_signature(task.id, index, event.content),
                }
            )
        elif event.kind is EventKind.TOOL_CALL:
            try:
                tool_input = json.loads(event.tool_args_json or "{}")
            except json.JSONDecodeError:
                tool_input = {}
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call_id(task.id, tool_index, prefix="toolu"),
                    "name": event.tool_name,
                    "input": tool_input,
                }
            )
            tool_index += 1
        else:
            content.append({"type": "text", "text": event.content})
    return {
        "id": f"msg_{task.id}",
        "type": "message",
        "role": "assistant",
        "model": task.model,
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": output_tokens(events)},
    }


async def anthropic_stream(
    task: RequestTask,
    events: list[ParsedEvent],
    settings: Settings,
    on_complete: Callable[[], None],
) -> AsyncIterator[str]:
    message = anthropic_json(task, [])
    message["stop_reason"] = None
    yield sse("message_start", {"type": "message_start", "message": message})
    streamer = pseudo_streamer(settings)
    tool_index = 0
    for block_index, event in enumerate(events):
        if event.kind is EventKind.REASONING:
            yield sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                },
            )
            async for part in streamer.text_chunks(event.content):
                yield sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "thinking_delta", "thinking": part},
                    },
                )
            yield sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": thinking_signature(task.id, block_index, event.content),
                    },
                },
            )
        elif event.kind is EventKind.TOOL_CALL:
            yield sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_call_id(task.id, tool_index, prefix="toolu"),
                        "name": event.tool_name,
                        "input": {},
                    },
                },
            )
            async for part in streamer.text_chunks(event.tool_args_json or "{}"):
                yield sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": part},
                    },
                )
            tool_index += 1
        else:
            yield sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            async for part in streamer.text_chunks(event.content):
                yield sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "text_delta", "text": part},
                    },
                )
        yield sse(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )
    yield sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens(events)},
        },
    )
    yield sse("message_stop", {"type": "message_stop"})
    on_complete()

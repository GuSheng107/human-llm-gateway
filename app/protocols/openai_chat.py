from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..config import Settings
from ..dsl import ParsedEvent
from ..enums import EventKind
from ..models import RequestTask
from .common import output_tokens, pseudo_streamer, tool_call_id


def openai_chat_json(task: RequestTask, events: list[ParsedEvent]) -> dict[str, Any]:
    reasoning = "".join(event.content for event in events if event.kind is EventKind.REASONING)
    final = "".join(event.content for event in events if event.kind is EventKind.FINAL)
    tools = [
        {
            "id": tool_call_id(task.id, index),
            "type": "function",
            "function": {"name": event.tool_name, "arguments": event.tool_args_json or "{}"},
        }
        for index, event in enumerate(
            event for event in events if event.kind is EventKind.TOOL_CALL
        )
    ]
    message: dict[str, Any] = {"role": "assistant", "content": final}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tools:
        message["tool_calls"] = tools
    completion_tokens = output_tokens(events)
    return {
        "id": f"chatcmpl-{task.id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": task.model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": completion_tokens,
            "total_tokens": completion_tokens,
        },
    }


async def openai_chat_stream(
    task: RequestTask,
    events: list[ParsedEvent],
    settings: Settings,
    on_complete: Callable[[], None],
) -> AsyncIterator[str]:
    base = {
        "id": f"chatcmpl-{task.id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": task.model,
    }
    yield f": task_id={task.id}\n\n"
    first = {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
    tool_index = 0
    streamer = pseudo_streamer(settings)
    for event in events:
        if event.kind is EventKind.TOOL_CALL:
            call_id = tool_call_id(task.id, tool_index)
            start_delta = {
                "tool_calls": [
                    {
                        "index": tool_index,
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": event.tool_name,
                            "arguments": "",
                        },
                    }
                ]
            }
            chunk = {
                **base,
                "choices": [
                    {"index": 0, "delta": start_delta, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            async for part in streamer.text_chunks(event.tool_args_json or "{}"):
                argument_delta = {
                    "tool_calls": [
                        {
                            "index": tool_index,
                            "function": {"arguments": part},
                        }
                    ]
                }
                chunk = {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": argument_delta,
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            tool_index += 1
            continue
        async for part in streamer.text_chunks(event.content):
            field = "reasoning_content" if event.kind is EventKind.REASONING else "content"
            chunk = {
                **base,
                "choices": [{"index": 0, "delta": {field: part}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    end = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(end, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
    on_complete()

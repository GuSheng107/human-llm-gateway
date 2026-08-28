from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..config import Settings
from ..dsl import ParsedEvent
from ..enums import EventKind
from ..models import RequestTask
from .common import output_tokens, pseudo_streamer, sse, tool_call_id


def _output_items(task: RequestTask, events: list[ParsedEvent]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    tool_index = 0
    message_index = 0
    reasoning_index = 0
    for event in events:
        if event.kind is EventKind.REASONING:
            output.append(
                {
                    "id": f"rs_{task.id.replace('-', '')[:16]}_{reasoning_index}",
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [{"type": "summary_text", "text": event.content}],
                }
            )
            reasoning_index += 1
        elif event.kind is EventKind.TOOL_CALL:
            call_id = tool_call_id(task.id, tool_index)
            output.append(
                {
                    "id": f"fc_{task.id.replace('-', '')[:16]}_{tool_index}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": event.tool_name,
                    "arguments": event.tool_args_json or "{}",
                }
            )
            tool_index += 1
        else:
            output.append(
                {
                    "id": f"msg_{task.id.replace('-', '')[:16]}_{message_index}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": event.content,
                            "annotations": [],
                        }
                    ],
                }
            )
            message_index += 1
    return output


def openai_responses_json(
    task: RequestTask,
    events: list[ParsedEvent],
    *,
    status: str = "completed",
    include_output: bool = True,
) -> dict[str, Any]:
    completed = status == "completed"
    return {
        "id": f"resp_{task.id}",
        "object": "response",
        "created_at": int(time.time()),
        "completed_at": int(time.time()) if completed else None,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": task.model,
        "output": _output_items(task, events) if include_output else [],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": (
            {
                "input_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": output_tokens(events),
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": output_tokens(events),
            }
            if completed
            else None
        ),
        "metadata": {},
    }


async def openai_responses_stream(
    task: RequestTask,
    events: list[ParsedEvent],
    settings: Settings,
    on_complete: Callable[[], None],
) -> AsyncIterator[str]:
    sequence = 0

    def event(event_type: str, **payload: Any) -> str:
        nonlocal sequence
        body = {"type": event_type, "sequence_number": sequence, **payload}
        sequence += 1
        return sse(event_type, body)

    pending = openai_responses_json(task, [], status="in_progress", include_output=False)
    yield event("response.created", response=pending)
    yield event("response.in_progress", response=pending)
    streamer = pseudo_streamer(settings)
    tool_index = 0
    reasoning_index = 0
    message_index = 0

    for output_index, parsed in enumerate(events):
        if parsed.kind is EventKind.REASONING:
            item_id = f"rs_{task.id.replace('-', '')[:16]}_{reasoning_index}"
            item = {"id": item_id, "type": "reasoning", "status": "in_progress", "summary": []}
            yield event("response.output_item.added", output_index=output_index, item=item)
            part = {"type": "summary_text", "text": ""}
            yield event(
                "response.reasoning_summary_part.added",
                item_id=item_id,
                output_index=output_index,
                summary_index=0,
                part=part,
            )
            async for chunk in streamer.text_chunks(parsed.content):
                yield event(
                    "response.reasoning_summary_text.delta",
                    item_id=item_id,
                    output_index=output_index,
                    summary_index=0,
                    delta=chunk,
                )
            yield event(
                "response.reasoning_summary_text.done",
                item_id=item_id,
                output_index=output_index,
                summary_index=0,
                text=parsed.content,
            )
            done_part = {"type": "summary_text", "text": parsed.content}
            yield event(
                "response.reasoning_summary_part.done",
                item_id=item_id,
                output_index=output_index,
                summary_index=0,
                part=done_part,
            )
            done_item = {
                "id": item_id,
                "type": "reasoning",
                "status": "completed",
                "summary": [done_part],
            }
            yield event("response.output_item.done", output_index=output_index, item=done_item)
            reasoning_index += 1
        elif parsed.kind is EventKind.TOOL_CALL:
            item_id = f"fc_{task.id.replace('-', '')[:16]}_{tool_index}"
            call_id = tool_call_id(task.id, tool_index)
            item = {
                "id": item_id,
                "type": "function_call",
                "status": "in_progress",
                "call_id": call_id,
                "name": parsed.tool_name,
                "arguments": "",
            }
            yield event("response.output_item.added", output_index=output_index, item=item)
            arguments = parsed.tool_args_json or "{}"
            async for chunk in streamer.text_chunks(arguments):
                yield event(
                    "response.function_call_arguments.delta",
                    item_id=item_id,
                    output_index=output_index,
                    delta=chunk,
                )
            yield event(
                "response.function_call_arguments.done",
                item_id=item_id,
                output_index=output_index,
                arguments=arguments,
            )
            yield event(
                "response.output_item.done",
                output_index=output_index,
                item={**item, "status": "completed", "arguments": arguments},
            )
            tool_index += 1
        else:
            item_id = f"msg_{task.id.replace('-', '')[:16]}_{message_index}"
            item = {
                "id": item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            yield event("response.output_item.added", output_index=output_index, item=item)
            part = {"type": "output_text", "text": "", "annotations": []}
            yield event(
                "response.content_part.added",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                part=part,
            )
            async for chunk in streamer.text_chunks(parsed.content):
                yield event(
                    "response.output_text.delta",
                    item_id=item_id,
                    output_index=output_index,
                    content_index=0,
                    delta=chunk,
                    logprobs=[],
                )
            done_part = {"type": "output_text", "text": parsed.content, "annotations": []}
            yield event(
                "response.output_text.done",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                text=parsed.content,
                logprobs=[],
            )
            yield event(
                "response.content_part.done",
                item_id=item_id,
                output_index=output_index,
                content_index=0,
                part=done_part,
            )
            yield event(
                "response.output_item.done",
                output_index=output_index,
                item={**item, "status": "completed", "content": [done_part]},
            )
            message_index += 1
    completed = openai_responses_json(task, events)
    yield event("response.completed", response=completed)
    on_complete()

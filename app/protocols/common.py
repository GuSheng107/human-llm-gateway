from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from ..config import Settings
from ..dsl import ParsedEvent
from ..streaming import PseudoStreamer


def tool_call_id(task_id: str, index: int, *, prefix: str = "call") -> str:
    return f"{prefix}_{task_id.replace('-', '')[:12]}_{index}"


def thinking_signature(task_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{index}:{text}".encode()).hexdigest()
    return f"human_llm_{digest}"


def approximate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def output_tokens(events: list[ParsedEvent]) -> int:
    payload = "".join(event.content or event.tool_args_json or "" for event in events)
    return approximate_tokens(payload)


def pseudo_streamer(settings: Settings) -> PseudoStreamer:
    return PseudoStreamer(
        settings.stream_chunk_size,
        settings.stream_delay_min_ms,
        settings.stream_delay_max_ms,
    )


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def chunk_text(text: str, settings: Settings) -> AsyncIterator[str]:
    async for part in pseudo_streamer(settings).text_chunks(text):
        yield part

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Iterable

from .dsl import ParsedEvent


class PseudoStreamer:
    def __init__(self, chunk_size: int = 24, delay_min_ms: int = 20, delay_max_ms: int = 90,
                 rng: random.Random | None = None, sleep: Callable[[float], object] | None = None):
        self.chunk_size = max(1, chunk_size)
        self.delay_min_ms = max(0, delay_min_ms)
        self.delay_max_ms = max(self.delay_min_ms, delay_max_ms)
        self.rng = rng or random.Random()
        self.sleep = sleep or asyncio.sleep

    async def events(self, events: Iterable[ParsedEvent]) -> AsyncIterator[ParsedEvent]:
        for event in events:
            if event.kind.value == "tool_call":
                yield event
                continue
            for index in range(0, len(event.content), self.chunk_size):
                part = event.content[index:index + self.chunk_size]
                delay = self.rng.uniform(self.delay_min_ms, self.delay_max_ms) / 1000
                if delay:
                    await self.sleep(delay)
                yield ParsedEvent(event.kind, part, event.tool_name, event.tool_args_json)


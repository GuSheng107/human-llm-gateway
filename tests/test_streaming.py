import random

import pytest

from app.dsl import ParsedEvent
from app.enums import EventKind
from app.streaming import PseudoStreamer


@pytest.mark.asyncio
async def test_pseudo_stream_is_reproducible_without_sleep():
    sleeps: list[float] = []

    async def no_sleep(delay: float) -> None:
        sleeps.append(delay)

    stream = PseudoStreamer(chunk_size=2, delay_min_ms=10, delay_max_ms=10,
                            rng=random.Random(1), sleep=no_sleep)
    result = [event async for event in stream.events([ParsedEvent(EventKind.FINAL, "abcd")])]
    assert [event.content for event in result] == ["ab", "cd"]
    assert sleeps == [0.01, 0.01]

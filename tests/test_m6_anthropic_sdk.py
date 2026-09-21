"""Anthropic Messages SDK 兼容契约测试（方案4）。

使用官方 anthropic SDK 直接解析网关返回：非流式 Message 与流式事件
序列都必须可被 SDK 消费；usage 正确；人工路径不返回 thinking block。

等待人工回复统一走 sdk_wait_helpers 的轮询：固定 sleep 在负载抖动下会漏，
漏掉时回复协程静默死亡、请求永久挂起（人工回复端点没有超时）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from anthropic import AsyncAnthropic

from tests.sdk_wait_helpers import (
    SDK_CALL_TIMEOUT_SECONDS,
    finish_reply_task,
    latest_task_id,
    start_reply_task,
)

_MODEL = "claude-sonnet-5"


@pytest.fixture
async def async_client(client) -> Any:
    # anthropic SDK 锁定 httpx2（fork）：使用其自有 ASGITransport 直连测试应用。
    import httpx2

    transport = httpx2.ASGITransport(app=client.app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def anthropic_sdk(async_client: Any, created_key) -> Any:
    return AsyncAnthropic(
        api_key=created_key.plaintext,
        base_url="http://test",  # SDK 自动拼接 /v1/messages
        http_client=async_client,
    )


@pytest.mark.asyncio
async def test_anthropic_non_stream_sdk_parseable(anthropic_sdk: Any, created_key: Any) -> None:
    runner = start_reply_task(
        created_key.id,
        created_key.owner_user_id,
        reasoning="思考过程",
        final_text="今天晴",
        after_id=latest_task_id(created_key.id) or 0,
    )
    try:
        async with asyncio.timeout(SDK_CALL_TIMEOUT_SECONDS):
            message = await anthropic_sdk.messages.create(
                model=_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": "北京天气如何"}],
            )
    finally:
        await finish_reply_task(runner)
    assert message.type == "message"
    assert message.role == "assistant"
    assert message.stop_reason == "end_turn"
    # usage 完整：input 来自请求消息，output 来自正文（无 thinking 块）。
    assert message.usage.input_tokens >= 1
    assert message.usage.output_tokens >= 1
    kinds = [block.type for block in message.content]
    # 人工路径不返回 thinking block（无有效 signature）。
    assert "thinking" not in kinds
    assert kinds == ["text"]
    assert message.content[0].text == "今天晴"


@pytest.mark.asyncio
async def test_anthropic_stream_sdk_parseable(anthropic_sdk: Any, created_key: Any) -> None:
    runner = start_reply_task(
        created_key.id,
        created_key.owner_user_id,
        reasoning="思考过程",
        final_text="今天晴",
        after_id=latest_task_id(created_key.id) or 0,
    )
    try:
        async with asyncio.timeout(SDK_CALL_TIMEOUT_SECONDS):
            async with anthropic_sdk.messages.stream(
                model=_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": "上海天气如何"}],
            ) as stream:
                text = await stream.get_final_text()
                final_message = await stream.get_final_message()
    finally:
        await finish_reply_task(runner)
    assert text == "今天晴"
    assert final_message.stop_reason == "end_turn"
    assert final_message.usage.input_tokens >= 1
    assert final_message.usage.output_tokens >= 1
    kinds = [block.type for block in final_message.content]
    assert "thinking" not in kinds

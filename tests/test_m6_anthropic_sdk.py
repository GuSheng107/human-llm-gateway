"""Anthropic Messages SDK 兼容契约测试（方案4）。

使用官方 anthropic SDK 直接解析网关返回：非流式 Message 与流式事件
序列都必须可被 SDK 消费；usage 正确；人工路径不返回 thinking block。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from anthropic import AsyncAnthropic

import app.core.db as database
from app.domain.values import ReplyDraft
from app.repositories.models import RequestTask
from app.services.inference_service import InferenceService

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


def _submit_reply(task_id: int, owner_user_id: int) -> None:
    draft = ReplyDraft(reasoning="思考过程", final_text="今天晴")
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        InferenceService().tasks.first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=owner_user_id,
            expected_version=task.version,
            response_payload_json=draft.model_dump_json(exclude_none=True),
        )
        session.commit()


def _latest_task_id_for_key(api_key_id: int) -> int:
    with database.SessionLocal() as session:
        row = (
            session.query(RequestTask)
            .filter(RequestTask.api_key_id == api_key_id)
            .order_by(RequestTask.id.desc())
            .first()
        )
        assert row is not None
        return row.id


@pytest.mark.asyncio
async def test_anthropic_non_stream_sdk_parseable(anthropic_sdk: Any, created_key: Any) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    message = await anthropic_sdk.messages.create(
        model=_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": "北京天气如何"}],
    )
    runner.cancel()
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
    async def reply_later() -> None:
        await asyncio.sleep(0.6)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    async with anthropic_sdk.messages.stream(
        model=_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": "上海天气如何"}],
    ) as stream:
        text = await stream.get_final_text()
        final_message = await stream.get_final_message()
    runner.cancel()
    assert text == "今天晴"
    assert final_message.stop_reason == "end_turn"
    assert final_message.usage.input_tokens >= 1
    assert final_message.usage.output_tokens >= 1
    kinds = [block.type for block in final_message.content]
    assert "thinking" not in kinds

"""OpenAI Responses SDK 兼容契约测试（方案4）。

使用官方 openai SDK 直接解析网关返回：非流式 Response 对象与流式事件
序列都必须可以被 SDK 消费；usage / annotations / sequence_number /
content part 事件齐全。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

import app.core.db as database
from app.domain.values import ReplyDraft
from app.repositories.models import RequestTask
from app.services.inference_service import InferenceService

_MODEL = "deepseek-v4-pro"


@pytest.fixture
async def async_client(client) -> Any:
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def responses_openai_client(async_client: Any, created_key) -> Any:
    return AsyncOpenAI(
        api_key=created_key.plaintext,
        base_url="http://test/v1",
        http_client=async_client,
    )


def _submit_reply(task_id: int, owner_user_id: int) -> None:
    draft = ReplyDraft(reasoning="先想一下", final_text="今天晴")
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
async def test_responses_non_stream_sdk_parseable(
    responses_openai_client: Any, created_key: Any
) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    response = await responses_openai_client.responses.create(
        model=_MODEL,
        input="北京天气如何",
    )
    runner.cancel()
    # SDK 解析成功即证明结构兼容；再校验关键字段。
    assert response.object == "response"
    assert response.status == "completed"
    assert response.usage is not None
    assert response.usage.input_tokens >= 1
    assert response.usage.output_tokens >= 1
    assert response.usage.total_tokens >= 2
    assert response.output_text == "今天晴"
    # 消息输出项带 id 与 annotations。
    message = next(item for item in response.output if item.type == "message")
    assert message.id
    for block in message.content:
        assert block.annotations == []


@pytest.mark.asyncio
async def test_responses_stream_sdk_parseable(
    responses_openai_client: Any, created_key: Any
) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.6)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    stream = await responses_openai_client.responses.create(
        model=_MODEL,
        input="上海天气如何",
        stream=True,
    )
    created = None
    in_progress = None
    completed = None
    text_done_events: list[str] = []
    sequence_numbers: list[int] = []
    async for event in stream:
        sequence_numbers.append(event.sequence_number)
        if event.type == "response.created":
            created = event
        elif event.type == "response.in_progress":
            in_progress = event
        elif event.type == "response.output_text.done":
            text_done_events.append(event.text)
        elif event.type == "response.completed":
            completed = event
    runner.cancel()
    assert created is not None
    assert in_progress is not None
    assert completed is not None
    assert text_done_events == ["今天晴"]
    assert sequence_numbers == sorted(sequence_numbers)
    # completed 事件携带完整 response（含 usage 与输出项）。
    final = completed.response
    assert final.status == "completed"
    assert final.usage is not None
    assert final.usage.output_tokens >= 1
    assert final.output_text == "今天晴"


@pytest.mark.asyncio
async def test_chat_completions_usage_and_include_usage(
    responses_openai_client: Any, created_key: Any
) -> None:
    """Chat 非流式 usage 与 stream_options.include_usage 流式帧。"""

    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    completion = await responses_openai_client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": "广州天气如何"}],
    )
    runner.cancel()
    assert completion.usage is not None
    assert completion.usage.prompt_tokens >= 1
    assert completion.usage.completion_tokens >= 1
    assert completion.usage.total_tokens >= 2

    runner = asyncio.create_task(reply_later())
    stream = await responses_openai_client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": "深圳天气如何"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    ids: set[str] = set()
    usage_chunk = None
    finish_reasons: list[str] = []
    async for chunk in stream:
        ids.add(chunk.id)
        if chunk.usage is not None:
            usage_chunk = chunk
        for choice in chunk.choices:
            if choice.finish_reason:
                finish_reasons.append(choice.finish_reason)
    runner.cancel()
    # 同一次流式响应稳定 ID；usage 帧带空 choices。
    assert len(ids) == 1
    assert usage_chunk is not None
    assert usage_chunk.usage is not None
    assert usage_chunk.usage.total_tokens >= 2
    assert usage_chunk.choices == []
    assert finish_reasons == ["stop"]

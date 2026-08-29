"""Chat Completions 流式中断契约：openai Python SDK 行为验证（§16.4）。

锁定 openai 依赖版本后必须重新执行：本测试决定 Chat 流内错误的表示
方式（error frame + EOF 触发 SDK APIError，client cancel 走
caller_disconnected 取消任务并释放名额）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from openai import APIError, AsyncOpenAI

import app.core.db as database
from app.domain.enums import TaskState
from app.domain.values import ReplyDraft
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import RequestTask
from app.services.inference_service import InferenceService


@pytest.fixture
async def async_client(client) -> Any:
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def openai_client(async_client: Any, created_key) -> Any:
    return AsyncOpenAI(
        api_key=created_key.plaintext,
        base_url="http://test/v1",
        http_client=async_client,
    )


def _submit_reply(task_id: int, owner_user_id: int) -> None:
    draft = ReplyDraft(final_text="done")
    payload = draft.model_dump_json(exclude_none=True)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        InferenceService().tasks.first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=owner_user_id,
            expected_version=task.version,
            response_payload_json=payload,
        )
        session.commit()


def _task(task_id: int) -> RequestTask:
    with database.SessionLocal() as session:
        return session.get(RequestTask, task_id)


@pytest.mark.asyncio
async def test_chat_stream_normal_completion(async_client, created_user, created_key) -> None:
    request_payload = {
        "model": "human-gateway",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }

    async def reply_later() -> None:
        await asyncio.sleep(0.6)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created_key.plaintext}"},
        json=request_payload,
    )
    runner.cancel()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    chunks: list[dict[str, Any]] = []
    async for line in response.aiter_lines():
        if line.startswith("data:") and not line.startswith("data: [DONE]"):
            import json as _json

            chunks.append(_json.loads(line[5:].strip()))
    assert chunks
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] in ("stop", "tool_calls")


@pytest.mark.asyncio
async def test_chat_stream_midstream_error_raises_api_error(
    async_client, created_user, created_key, monkeypatch
) -> None:
    """流内 error frame + EOF 触发 openai SDK APIError（§16.4 决策固化）。"""
    original_stream = chat_protocol.stream_frames

    def boom(_model: str, _draft: ReplyDraft):
        generator = original_stream(_model, _draft)
        yield next(generator)  # 仅首帧，随后中断
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_protocol, "stream_frames", boom)

    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id)

    runner = asyncio.create_task(reply_later())
    sdk = AsyncOpenAI(
        api_key=created_key.plaintext, base_url="http://test/v1", http_client=async_client
    )
    with pytest.raises(APIError) as exc:
        stream = await sdk.chat.completions.create(
            model="human-gateway", messages=[{"role": "user", "content": "hi"}], stream=True
        )
        async for _chunk in stream:
            pass
    runner.cancel()
    assert "server had an error" in str(exc.value).lower()


class _DisconnectInjector:
    """测试专用 ASGI 包装：请求体转发完毕后，disconnect 置位即返回 http.disconnect。

    模拟客户端在等待人工回复期间断开（生产环境由 ASGI 服务器送达真实断开；
    httpx ASGITransport 在响应完成前不会自然产生 disconnect，必须注入）。
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self.disconnect = asyncio.Event()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        body_done = False

        async def wrapped_receive() -> dict[str, Any]:
            nonlocal body_done
            if body_done:
                await self.disconnect.wait()
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request" and not message.get("more_body"):
                body_done = True
            return message

        await self._app(scope, wrapped_receive, send)


@pytest.mark.asyncio
async def test_chat_stream_caller_disconnected_cancels_task(
    client, created_user, created_key
) -> None:
    """等待人工回复期间客户端断开：任务取消并释放名额，返回 499。"""
    injector = _DisconnectInjector(client.app)
    transport = httpx.ASGITransport(app=injector)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:

        async def disconnect_later() -> None:
            await asyncio.sleep(0.4)
            injector.disconnect.set()

        runner = asyncio.create_task(disconnect_later())
        response = await ac.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {created_key.plaintext}"},
            json={
                "model": "human-gateway",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        await runner

    assert response.status_code == 499
    task_id = _latest_task_id_for_key(created_key.id)
    task = _task(task_id)
    assert task.state is TaskState.CANCELLED
    assert task.cancel_reason_code == "caller_disconnected"


def _latest_task_id_for_key(api_key_id: int) -> int:
    from sqlalchemy import select

    with database.SessionLocal() as session:
        row = (
            session.execute(
                select(RequestTask)
                .where(RequestTask.api_key_id == api_key_id)
                .order_by(RequestTask.id.desc())
            )
            .scalars()
            .first()
        )
        return row.id

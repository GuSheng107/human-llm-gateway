"""Chat Completions 流式中断契约：openai Python SDK 行为验证（§16.4）。

锁定 openai 依赖版本后必须重新执行：本测试决定 Chat 流内错误的表示
方式（error frame + EOF 触发 SDK APIError，client cancel 走
caller_disconnected 取消任务并释放名额）。

等待人工回复统一走 sdk_wait_helpers 的轮询：固定 sleep 在负载抖动下会漏，
漏掉时回复协程静默死亡、请求永久挂起（人工回复端点没有超时）。
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
from tests.sdk_wait_helpers import (
    SDK_CALL_TIMEOUT_SECONDS,
    finish_reply_task,
    latest_task_id,
    start_reply_task,
    wait_for_task_state,
)


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


def _start_reply(created_key: Any) -> asyncio.Task[None]:
    """以调用前的最新任务为基线发起回复，确保命中的是本次请求新建的任务。"""
    return start_reply_task(
        created_key.id,
        created_key.owner_user_id,
        final_text="done",
        after_id=latest_task_id(created_key.id) or 0,
    )


def _task(task_id: int) -> RequestTask:
    with database.SessionLocal() as session:
        return session.get(RequestTask, task_id)


@pytest.mark.asyncio
async def test_chat_stream_normal_completion(async_client, created_user, created_key) -> None:
    request_payload = {
        "model": "deepseek-v4-pro",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }

    runner = _start_reply(created_key)
    try:
        async with asyncio.timeout(SDK_CALL_TIMEOUT_SECONDS):
            response = await async_client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {created_key.plaintext}"},
                json=request_payload,
            )
            assert response.status_code == 200
            chunks: list[dict[str, Any]] = []
            async for line in response.aiter_lines():
                if line.startswith("data:") and not line.startswith("data: [DONE]"):
                    import json as _json

                    chunks.append(_json.loads(line[5:].strip()))
    finally:
        await finish_reply_task(runner)
    assert response.headers["content-type"].startswith("text/event-stream")
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

    runner = _start_reply(created_key)
    sdk = AsyncOpenAI(
        api_key=created_key.plaintext, base_url="http://test/v1", http_client=async_client
    )
    try:
        with pytest.raises(APIError) as exc:
            async with asyncio.timeout(SDK_CALL_TIMEOUT_SECONDS):
                stream = await sdk.chat.completions.create(
                    model="deepseek-v4-pro",
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
                async for _chunk in stream:
                    pass
    finally:
        await finish_reply_task(runner)
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
            # 必须等任务真正进入等待人工回复再注入断开；固定 sleep 在负载
            # 抖动下会早于任务落库，断开落在请求处理前半段而测不到取消路径。
            await wait_for_task_state(created_key.id, TaskState.WAITING_HUMAN, after_id=0)
            injector.disconnect.set()

        runner = asyncio.create_task(disconnect_later())
        response = await ac.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {created_key.plaintext}"},
            json={
                "model": "deepseek-v4-pro",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        await runner

    assert response.status_code == 499
    task_id = latest_task_id(created_key.id)
    assert task_id is not None
    task = _task(task_id)
    assert task.state is TaskState.CANCELLED
    assert task.cancel_reason_code == "caller_disconnected"

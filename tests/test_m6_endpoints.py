"""M6 三协议端点契约测试（docs/API_CONTRACT.md §12-§16）。

覆盖 happy（非流式/流式）、超时 504、model_not_found 404、鉴权 401、
payload_too_large 413、并发名额 429。调用方断开取消由
test_m6_chat_stream_sdk.py 单独固化（openai SDK 行为契约）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

import app.core.db as database
from app.domain.enums import InferenceProtocol, TaskState
from app.domain.values import ReplyDraft
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, RequestTask, User
from app.services.inference_service import InferenceService

# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _bearer(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def _anthropic_headers(plaintext: str) -> dict[str, str]:
    return {"x-api-key": plaintext, "anthropic-version": "2023-06-01"}


def _chat_payload(**extra: Any) -> dict[str, Any]:
    return {"model": "human-gateway", "messages": [{"role": "user", "content": "hi"}], **extra}


def _responses_payload(**extra: Any) -> dict[str, Any]:
    return {"model": "human-gateway", "input": "hi", **extra}


def _anthropic_payload(**extra: Any) -> dict[str, Any]:
    return {
        "model": "human-gateway",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
        **extra,
    }


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
        assert row is not None, "未找到任务"
        return row.id


def _submit_reply(task_id: int, owner_user_id: int, *, text: str = "done") -> None:
    draft = ReplyDraft(final_text=text)
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


def _finalize_state(task_id: int, state: TaskState) -> None:
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is not None:
            InferenceService().finalize(session, task, state)
            session.commit()


def _fill_active_slots(n: int, key_id: int, owner_user_id: int) -> None:
    """直接经编排服务创建 n 个 WAITING_HUMAN 任务以占满用户名额。"""
    raw = json.dumps(_chat_payload()).encode()
    parsed = chat_protocol.parse_request(raw)
    service = InferenceService()
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, owner_user_id)
        for _ in range(n):
            service.create_task(
                session,
                key=key,
                owner=owner,
                protocol=InferenceProtocol.OPENAI_CHAT,
                parsed=parsed,
                raw_body=raw,
                headers={},
            )
            session.commit()


def _sse_data_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        events.append(json.loads(data))
    return events


@pytest.fixture
async def async_client(client) -> Any:
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ----------------------------------------------------------------------
# 鉴权 401
# ----------------------------------------------------------------------


def test_chat_no_api_key_returns_401(client) -> None:
    resp = client.post("/v1/chat/completions", json=_chat_payload())
    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["type"] == "invalid_request_error"
    assert body["code"] == "invalid_api_key"


def test_chat_invalid_api_key_returns_401(client) -> None:
    resp = client.post(
        "/v1/chat/completions",
        headers=_bearer("sk-deadbeef"),
        json=_chat_payload(),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


def test_anthropic_no_api_key_returns_401(client) -> None:
    resp = client.post("/v1/messages", json=_anthropic_payload())
    assert resp.status_code == 401
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "authentication_error"


def test_anthropic_invalid_api_key_returns_401(client) -> None:
    resp = client.post(
        "/v1/messages",
        headers=_anthropic_headers("sk-deadbeef"),
        json=_anthropic_payload(),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


# ----------------------------------------------------------------------
# 模型不存在 404
# ----------------------------------------------------------------------


def test_chat_model_not_found_returns_404(client, created_key) -> None:
    resp = client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(model="no-such-model"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


def test_responses_model_not_found_returns_404(client, created_key) -> None:
    resp = client.post(
        "/v1/responses",
        headers=_bearer(created_key.plaintext),
        json=_responses_payload(model="no-such-model"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


def test_anthropic_model_not_found_returns_404(client, created_key) -> None:
    resp = client.post(
        "/v1/messages",
        headers=_anthropic_headers(created_key.plaintext),
        json=_anthropic_payload(model="no-such-model"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found_error"


# ----------------------------------------------------------------------
# 请求体超限 413
# ----------------------------------------------------------------------


def test_chat_payload_too_large_returns_413(client, created_key, monkeypatch) -> None:
    monkeypatch.setattr("app.api.limits.MAX_INFERENCE_REQUEST_BYTES", 64)
    payload = _chat_payload(messages=[{"role": "user", "content": "x" * 200}])
    resp = client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=payload,
    )
    assert resp.status_code == 413
    body = resp.json()["error"]
    assert body["type"] == "invalid_request_error"
    assert body["code"] == "payload_too_large"


def test_anthropic_payload_too_large_returns_413(client, created_key, monkeypatch) -> None:
    monkeypatch.setattr("app.api.limits.MAX_INFERENCE_REQUEST_BYTES", 64)
    payload = _anthropic_payload(messages=[{"role": "user", "content": "x" * 200}])
    resp = client.post(
        "/v1/messages",
        headers=_anthropic_headers(created_key.plaintext),
        json=payload,
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["type"] == "request_too_large"


# ----------------------------------------------------------------------
# 并发名额 429
# ----------------------------------------------------------------------


def test_chat_active_task_limit_returns_429(client, created_key) -> None:
    _fill_active_slots(10, created_key.id, created_key.owner_user_id)
    resp = client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(),
    )
    assert resp.status_code == 429
    body = resp.json()["error"]
    assert body["type"] == "rate_limit_error"
    assert body["code"] == "rate_limit_exceeded"
    # 名额未被泄漏：再次请求仍 429。
    again = client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(),
    )
    assert again.status_code == 429


# ----------------------------------------------------------------------
# 人工超时 504
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_timeout_returns_504(async_client, created_key) -> None:
    async def timeout_later() -> None:
        await asyncio.sleep(0.4)
        task_id = _latest_task_id_for_key(created_key.id)
        _finalize_state(task_id, TaskState.TIMED_OUT)

    runner = asyncio.create_task(timeout_later())
    resp = await async_client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(),
    )
    await runner
    assert resp.status_code == 504
    body = resp.json()["error"]
    assert body["type"] == "timeout_error"
    assert body["code"] == "request_timeout"


@pytest.mark.asyncio
async def test_anthropic_timeout_returns_504(async_client, created_key) -> None:
    async def timeout_later() -> None:
        await asyncio.sleep(0.4)
        task_id = _latest_task_id_for_key(created_key.id)
        _finalize_state(task_id, TaskState.TIMED_OUT)

    runner = asyncio.create_task(timeout_later())
    resp = await async_client.post(
        "/v1/messages",
        headers=_anthropic_headers(created_key.plaintext),
        json=_anthropic_payload(),
    )
    await runner
    assert resp.status_code == 504
    assert resp.json()["error"]["type"] == "api_error"


# ----------------------------------------------------------------------
# happy 非流式
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_happy_nonstream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.4)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="你好")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(),
    )
    await runner
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "human-gateway"
    assert body["choices"][0]["message"]["content"] == "你好"
    assert body["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_responses_happy_nonstream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.4)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="hello")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/responses",
        headers=_bearer(created_key.plaintext),
        json=_responses_payload(),
    )
    await runner
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["model"] == "human-gateway"
    assert body["id"].startswith("resp_")
    assert body["output"]


@pytest.mark.asyncio
async def test_anthropic_happy_nonstream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.4)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="hi there")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/messages",
        headers=_anthropic_headers(created_key.plaintext),
        json=_anthropic_payload(),
    )
    await runner
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == "human-gateway"
    assert body["content"]
    assert body["stop_reason"] == "end_turn"


# ----------------------------------------------------------------------
# happy 伪流式
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_happy_stream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="流式")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/chat/completions",
        headers=_bearer(created_key.plaintext),
        json=_chat_payload(stream=True),
    )
    runner.cancel()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks = _sse_data_events(resp.text)
    assert chunks
    assert any("content" in c["choices"][0]["delta"] for c in chunks if c.get("choices"))
    assert resp.text.strip().endswith("[DONE]")


@pytest.mark.asyncio
async def test_responses_happy_stream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="r-stream")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/responses",
        headers=_bearer(created_key.plaintext),
        json=_responses_payload(stream=True),
    )
    runner.cancel()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    types = [e.get("type") for e in _sse_data_events(resp.text)]
    assert "response.created" in types
    assert "response.completed" in types


@pytest.mark.asyncio
async def test_anthropic_happy_stream(async_client, created_key) -> None:
    async def reply_later() -> None:
        await asyncio.sleep(0.5)
        task_id = _latest_task_id_for_key(created_key.id)
        _submit_reply(task_id, created_key.owner_user_id, text="a-stream")

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/messages",
        headers=_anthropic_headers(created_key.plaintext),
        json=_anthropic_payload(stream=True),
    )
    runner.cancel()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    types = [e.get("type") for e in _sse_data_events(resp.text)]
    assert "message_start" in types
    assert "message_stop" in types

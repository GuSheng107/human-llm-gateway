"""可控 HTTP 边界验证取消、预算、状态竞争与失败后的名额释放。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Request
from sqlalchemy import select

import app.core.db as database
from app.api import inference
from app.core.time import utc_now
from app.domain.enums import InferenceProtocol, TaskState
from app.domain.errors import DomainError, DomainErrorCode
from app.domain.values import ReplyDraft
from app.protocols import chat_completions
from app.repositories.models import ApiKey, RequestTask, TaskEvent, User
from app.repositories.tasks import TaskRepository
from app.services import llm_upstream
from app.services.inference_service import InferenceService

POSTS = (
    llm_upstream.post_chat_completions,
    llm_upstream.post_responses,
    llm_upstream.post_anthropic_messages,
)
STREAMS = (
    llm_upstream.stream_chat_completions,
    llm_upstream.stream_responses,
    llm_upstream.stream_anthropic_messages,
)
HTTP_ARGS = {
    "base_url": "https://upstream.example.com/v1",
    "api_key": "fictional-test-secret",
    "request_body": {"model": "upstream-test"},
    "timeout_seconds": 10,
}


class ControlledBody(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, block: bool = False) -> None:
        self.chunks = chunks
        self.block = block
        self.started = Event()
        self.release = Event()
        self.closed = Event()
        self.read_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk
        if self.block:
            while not self.release.is_set():
                await asyncio.sleep(0.005)

    async def aclose(self) -> None:
        self.closed.set()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable:
    real_client = httpx.AsyncClient

    def install(body: ControlledBody, status: int = 200) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                status,
                headers={"location": "http://169.254.169.254/latest"},
                stream=body,
            )

        def factory(**kwargs: object) -> httpx.AsyncClient:
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return requests

    return install


async def _consume(function: Callable) -> None:
    result = function(**HTTP_ARGS)
    if function in POSTS:
        await result
    else:
        async for _ in result:
            pass


@pytest.mark.parametrize("function", POSTS)
async def test_json_limit_stops_reading_before_eof(function, serve, monkeypatch) -> None:
    body = ControlledBody([b" " * 8, b" " * 8, b"never-read"], block=True)
    serve(body)
    monkeypatch.setattr(llm_upstream, "LLM_MAX_RESPONSE_BYTES", 12)
    with pytest.raises(DomainError) as exc:
        await asyncio.wait_for(_consume(function), timeout=2)
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR
    assert body.read_count == 2
    assert body.closed.is_set()


@pytest.mark.parametrize("function", POSTS + STREAMS)
@pytest.mark.parametrize("status", [302, 503])
async def test_rejected_status_does_not_read_body_or_redirect(function, status, serve) -> None:
    body = ControlledBody([b"never-read-secret"], block=True)
    requests = serve(body, status)
    with pytest.raises(DomainError) as exc:
        await asyncio.wait_for(_consume(function), timeout=2)
    assert exc.value.status_code == 502
    assert len(requests) == 1
    assert body.read_count == 0
    assert body.closed.is_set()
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("function", POSTS + STREAMS)
async def test_total_budget_interrupts_silent_body(function, serve, monkeypatch) -> None:
    body = ControlledBody([], block=True)
    serve(body)
    monkeypatch.setattr(llm_upstream, "LLM_MAX_STREAM_SECONDS", 0.05)
    with pytest.raises(DomainError) as exc:
        await asyncio.wait_for(_consume(function), timeout=2)
    assert exc.value.code is DomainErrorCode.REQUEST_TIMEOUT
    assert body.closed.is_set()


@pytest.mark.parametrize("function", POSTS + STREAMS)
async def test_total_budget_includes_waiting_for_headers(function, monkeypatch) -> None:
    real_client = httpx.AsyncClient
    cancelled = Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(llm_upstream, "LLM_MAX_STREAM_SECONDS", 0.05)
    with pytest.raises(DomainError) as exc:
        await asyncio.wait_for(_consume(function), timeout=2)
    assert exc.value.code is DomainErrorCode.REQUEST_TIMEOUT
    assert cancelled.is_set()


@pytest.mark.parametrize("function", STREAMS)
async def test_sse_unterminated_line_is_bounded_before_eof(function, serve, monkeypatch) -> None:
    body = ControlledBody([b"data: " + b"x" * 20, b"x" * 20, b"never-read"], block=True)
    serve(body)
    monkeypatch.setattr(llm_upstream, "LLM_MAX_SSE_LINE_BYTES", 30)
    with pytest.raises(DomainError):
        await asyncio.wait_for(_consume(function), timeout=2)
    assert body.read_count == 2
    assert body.closed.is_set()


@pytest.mark.parametrize("separator", [b"\n", b"\r", b"\r\n"])
async def test_sse_line_endings_and_utf8_split_across_every_byte(separator, serve) -> None:
    payload = {"choices": [{"delta": {"content": "中文"}}]}
    wire = b"data: " + json.dumps(payload, ensure_ascii=False).encode() + separator * 2
    wire += b"data: [DONE]" + separator * 2
    body = ControlledBody([bytes([byte]) for byte in wire])
    serve(body)
    chunks = [chunk async for chunk in llm_upstream.stream_chat_completions(**HTTP_ARGS)]
    assert "".join(chunk.text for chunk in chunks) == "中文"
    assert body.closed.is_set()


@pytest.mark.parametrize("function", POSTS + STREAMS)
async def test_every_http_entry_rechecks_address_before_request(
    function, serve, monkeypatch
) -> None:
    body = ControlledBody([])
    requests = serve(body)
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("169.254.169.254", 443))]
    )
    with pytest.raises(DomainError):
        await _consume(function)
    assert requests == []


def _key(client, user, *, strategy: str, protocol: str = "openai_chat") -> dict:
    config = client.post(
        "/api/llm-configs",
        headers=user.headers,
        json={
            "name": "controlled-upstream",
            "protocol": protocol,
            "base_url": HTTP_ARGS["base_url"],
            "api_key": "fictional-test-secret",
            "model": "upstream-test",
            "timeout_seconds": 60,
        },
    )
    assert config.status_code == 201, config.text
    key = client.post(
        "/api/api-keys",
        headers=user.headers,
        json={
            "name": "lifecycle-key",
            "delivery_mode": "web",
            "reply_strategy": strategy,
            "llm_config_id": int(config.json()["id"]),
        },
    )
    assert key.status_code == 201, key.text
    return key.json()


def _latest(key: dict) -> RequestTask:
    with database.SessionLocal() as session:
        return session.scalars(
            select(RequestTask).where(RequestTask.api_key_id == int(key["id"]))
        ).one()


def _assert_released(key: dict, state: TaskState) -> RequestTask:
    row = _latest(key)
    assert row.state is state
    assert row.slot_released_at is not None
    with database.SessionLocal() as session:
        assert session.get(User, row.owner_user_id).active_task_count == 0
        assert TaskRepository().count_active_for_user(session, row.owner_user_id) == 0
        assert not InferenceService().cancel_caller_disconnected(session, row.id)
        session.commit()
        events = list(session.scalars(select(TaskEvent).where(TaskEvent.task_id == row.id)))
    assert sum(event.event_type.value == state.value for event in events) == 1
    return row


async def _wait_started(body: ControlledBody) -> None:
    async with asyncio.timeout(3):
        while not body.started.is_set():
            await asyncio.sleep(0.005)


def _incoming(body: bytes, control: SimpleNamespace) -> Request:
    received = False

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        if control.disconnected:
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    return Request({"type": "http", "method": "POST", "headers": []}, receive)


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses", "anthropic_messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("strategy", ["llm", "human_fallback_llm"])
async def test_caller_disconnect_cancels_http_and_releases_once(
    client, created_user, protocol, stream, strategy, serve, monkeypatch
) -> None:
    key = _key(client, created_user, strategy=strategy, protocol=protocol)
    upstream = ControlledBody([], block=True)
    requests = serve(upstream)
    monkeypatch.setattr(inference, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(inference, "_now", lambda: utc_now() + timedelta(hours=1))
    control = SimpleNamespace(disconnected=False)
    body = json.dumps(
        {
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        }
    ).encode()
    with database.SessionLocal() as session:
        processing = asyncio.create_task(
            inference._handle(
                _incoming(body, control),
                InferenceProtocol.OPENAI_CHAT,
                chat_completions.parse_request,
                session.get(ApiKey, int(key["id"])),
                session.get(User, created_user.user_id),
                session,
            )
        )
        await _wait_started(upstream)
        control.disconnected = True
        assert await asyncio.wait_for(processing, timeout=3) is None
    assert len(requests) == 1
    assert upstream.closed.is_set()
    row = _assert_released(key, TaskState.CANCELLED)
    assert row.cancel_reason_code == "caller_disconnected"
    assert row.response_payload_json is None


@pytest.mark.parametrize("reason", ["request_cancelled", "user_disabled"])
async def test_task_cancellation_stops_upstream_and_preserves_terminal(
    client, created_user, reason, serve, monkeypatch
) -> None:
    key = _key(client, created_user, strategy="llm")
    upstream = ControlledBody([], block=True)
    serve(upstream)
    monkeypatch.setattr(inference, "_POLL_INTERVAL_SECONDS", 0.01)
    control = SimpleNamespace(disconnected=False)
    body = json.dumps(
        {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    with database.SessionLocal() as session:
        processing = asyncio.create_task(
            inference._handle(
                _incoming(body, control),
                InferenceProtocol.OPENAI_CHAT,
                chat_completions.parse_request,
                session.get(ApiKey, int(key["id"])),
                session.get(User, created_user.user_id),
                session,
            )
        )
        await _wait_started(upstream)
        if reason == "request_cancelled":
            processing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(processing, timeout=3)
        else:
            row = _latest(key)
            with database.SessionLocal() as other:
                InferenceService().cancel_caller_disconnected(other, row.id, reason=reason)
                other.commit()
            assert await asyncio.wait_for(processing, timeout=3) is None
    assert upstream.closed.is_set()
    row = _assert_released(key, TaskState.CANCELLED)
    assert row.cancel_reason_code == (
        "caller_disconnected" if reason == "request_cancelled" else reason
    )
    assert row.response_payload_json is None


@pytest.mark.parametrize("stream", [False, True])
def test_fallback_upstream_failure_is_failed_not_human_timeout(
    client, created_user, stream, serve, monkeypatch
) -> None:
    key = _key(client, created_user, strategy="human_fallback_llm")
    body = ControlledBody([], block=True)
    requests = serve(body, 503)
    monkeypatch.setattr(inference, "_now", lambda: utc_now() + timedelta(hours=1))
    monkeypatch.setattr(inference, "_POLL_INTERVAL_SECONDS", 0.01)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['plaintext']}"},
        json={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        },
    )
    assert response.status_code == 500
    assert "fallback" not in response.text.lower()
    assert len(requests) == 1
    assert body.closed.is_set()
    row = _assert_released(key, TaskState.FAILED)
    assert row.public_error_code == "upstream_error"
    assert row.response_payload_json is None


def _waiting(key: dict, user_id: int) -> int:
    raw = json.dumps(
        {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    with database.SessionLocal() as session:
        row = InferenceService().create_task(
            session,
            key=session.get(ApiKey, int(key["id"])),
            owner=session.get(User, user_id),
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=chat_completions.parse_request(raw),
            raw_body=raw,
            headers={},
        )
        session.commit()
        return row.id


async def test_duplicate_fallback_cannot_timeout_or_fail_current_owner(
    client, created_user, serve, monkeypatch
) -> None:
    key = _key(client, created_user, strategy="human_fallback_llm")
    task_id = _waiting(key, created_user.user_id)
    body = ControlledBody([], block=True)
    requests = serve(body)
    first = asyncio.create_task(inference._run_fallback(task_id))
    try:
        await _wait_started(body)
        assert await inference._run_fallback(task_id) is None
        assert not inference._finalize(task_id, TaskState.TIMED_OUT)
        assert _latest(key).state is TaskState.FORWARDING_LLM
        assert len(requests) == 1
        late_reply = client.post(
            f"/api/tasks/{task_id}/reply",
            headers=created_user.headers,
            json={"final_text": "late human answer"},
        )
        assert late_reply.status_code == 409
    finally:
        inference._cancel_disconnected(task_id)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert body.closed.is_set()
    _assert_released(key, TaskState.CANCELLED)


@pytest.mark.parametrize("cancel_before_start", [False, True])
async def test_cancelled_replay_never_reopens_or_emits_done(
    client, created_user, cancel_before_start
) -> None:
    key = _key(client, created_user, strategy="human_fallback_llm")
    task_id = _waiting(key, created_user.user_id)
    draft = ReplyDraft(final_text="abcdefghij")
    with database.SessionLocal() as session:
        row = session.get(RequestTask, task_id)
        assert TaskRepository().first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=created_user.user_id,
            expected_version=row.version,
            response_payload_json=draft.model_dump_json(),
        )
        session.commit()
    task = _latest(key)
    if cancel_before_start:
        inference._cancel_disconnected(task_id)
    generator = inference._stream(
        task,
        draft,
        lambda: chat_completions.stream_frames(task.requested_model, draft),
        chat_completions.stream_error_frame,
    )
    frames = []
    if not cancel_before_start:
        frames.append(await anext(generator))
        inference._cancel_disconnected(task_id)
    frames.extend([frame async for frame in generator])
    wire = b"".join(frame.encode() for frame in frames).decode()
    assert "[DONE]" not in wire
    assert "error" in wire
    assert not inference._mark_responding(task_id)
    _assert_released(key, TaskState.CANCELLED)


@pytest.mark.parametrize("send_point", ["json", "sse_last_frame", "sse_eof"])
@pytest.mark.parametrize("ending", ["success", "cancel", "send_error"])
async def test_slot_is_held_until_actual_final_send_finishes(
    client, created_user, send_point, ending
) -> None:
    from functools import partial

    from app.api.task_responses import TaskEventSourceResponse

    key = _key(client, created_user, strategy="human_fallback_llm")
    task_id = _waiting(key, created_user.user_id)
    draft = ReplyDraft(final_text="sent answer")
    with database.SessionLocal() as session:
        row = session.get(RequestTask, task_id)
        assert TaskRepository().first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=created_user.user_id,
            expected_version=row.version,
            response_payload_json=draft.model_dump_json(),
        )
        session.commit()
    row = _latest(key)
    if send_point != "json":
        response = TaskEventSourceResponse(
            inference._stream(
                row,
                draft,
                lambda: chat_completions.stream_frames(row.requested_model, draft),
                chat_completions.stream_error_frame,
            ),
            cancel=partial(inference._cancel_disconnected, task_id),
            complete=partial(inference._finalize, task_id, TaskState.COMPLETED),
        )
    else:
        response = inference._json_response(
            {"result": "sent answer"}, task_id=task_id, request_id=""
        )
    sending = asyncio.Event()
    release = asyncio.Event()

    async def receive() -> dict:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: dict) -> None:
        if message["type"] == "http.response.body" and (
            send_point == "json"
            or (send_point == "sse_last_frame" and b"[DONE]" in message.get("body", b""))
            or (send_point == "sse_eof" and not message.get("more_body", False))
        ):
            sending.set()
            await release.wait()
            if ending == "send_error":
                raise OSError("synthetic transport failure")

    scope = {"type": "http", "method": "POST", "headers": [], "asgi": {"version": "3.0"}}
    running = asyncio.create_task(response(scope, receive, send))
    await asyncio.wait_for(sending.wait(), timeout=3)
    assert _latest(key).state is TaskState.RESPONDING
    assert _latest(key).slot_released_at is None
    if ending == "cancel":
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=3)
    elif ending == "send_error":
        release.set()
        with pytest.raises((OSError, ExceptionGroup)):
            await asyncio.wait_for(running, timeout=3)
    else:
        release.set()
        await asyncio.wait_for(running, timeout=3)
    _assert_released(key, TaskState.COMPLETED if ending == "success" else TaskState.CANCELLED)


async def test_json_cancelled_after_reply_ready_never_sends_content(client, created_user) -> None:
    key = _key(client, created_user, strategy="human_fallback_llm")
    task_id = _waiting(key, created_user.user_id)
    with database.SessionLocal() as session:
        row = session.get(RequestTask, task_id)
        assert TaskRepository().first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=created_user.user_id,
            expected_version=row.version,
            response_payload_json=ReplyDraft(final_text="not sent").model_dump_json(),
        )
        session.commit()
    response = inference._json_response({"result": "not sent"}, task_id=task_id, request_id="")
    inference._cancel_disconnected(task_id)
    sent = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    with pytest.raises(DomainError):
        await response({"type": "http"}, receive, send)
    assert sent == []
    _assert_released(key, TaskState.CANCELLED)


async def test_cancel_during_admission_waits_for_commit_and_releases(
    client, created_user, monkeypatch
) -> None:
    key = _key(client, created_user, strategy="llm")
    entered = Event()
    release = Event()
    create = inference._create_task

    def blocked_create(*args):
        entered.set()
        assert release.wait(timeout=3)
        return create(*args)

    monkeypatch.setattr(inference, "_create_task", blocked_create)
    control = SimpleNamespace(disconnected=False)
    body = json.dumps(
        {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    with database.SessionLocal() as session:
        running = asyncio.create_task(
            inference._handle(
                _incoming(body, control),
                InferenceProtocol.OPENAI_CHAT,
                chat_completions.parse_request,
                session.get(ApiKey, int(key["id"])),
                session.get(User, created_user.user_id),
                session,
            )
        )
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=3)
    _assert_released(key, TaskState.CANCELLED)


async def test_repeated_cancel_waits_for_http_cleanup(client, created_user, serve) -> None:
    from app.services.forward_runtime import run_forward

    key = _key(client, created_user, strategy="llm")
    task_id = _waiting(key, created_user.user_id)
    closing = Event()
    allow_close = Event()

    class SlowClose(ControlledBody):
        async def aclose(self) -> None:
            closing.set()
            while not allow_close.is_set():
                await asyncio.sleep(0.005)
            await super().aclose()

    body = SlowClose([], block=True)
    serve(body)
    running = asyncio.create_task(run_forward(task_id, reason="direct", stream=False))
    try:
        await _wait_started(body)
        inference._cancel_disconnected(task_id)
        running.cancel()
        async with asyncio.timeout(3):
            while not closing.is_set():
                await asyncio.sleep(0.005)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done()
    finally:
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=3)
    assert body.closed.is_set()
    _assert_released(key, TaskState.CANCELLED)

"""三个推理协议入口（docs/API_CONTRACT.md §12-§16）。

生命周期：解析 -> Key 鉴权 -> 准入 -> 建任务并投递 -> 等待人工回复 ->
非流式 JSON / 伪流式 SSE -> 终态与名额幂等释放。
调用方断开、人工超时与内部异常对外只返回协议兼容的通用错误。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from ..core.db import get_db
from ..core.logging import get_request_id
from ..domain.enums import InferenceProtocol, TaskState
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from ..protocols import anthropic as anthropic_protocol
from ..protocols import chat_completions as chat_protocol
from ..protocols import responses as responses_protocol
from ..repositories.models import ApiKey, RequestTask, User
from ..services.inference_service import InferenceService
from .v1_models import require_api_key

router = APIRouter(prefix="/v1", tags=["v1-inference"])

_service = InferenceService()

# 轮询间隔（秒）：等待人工回复期间的状态检查粒度。
_POLL_INTERVAL_SECONDS = 0.5


def _require_api_key_anthropic(
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[ApiKey, User]:
    """Anthropic 鉴权：优先 x-api-key，兼容 Authorization: Bearer。"""
    token = request.headers.get("x-api-key")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise DomainError(
            DomainErrorCode.INVALID_API_KEY,
            "x-api-key header is required",
            status_code=401,
        )
    from ..repositories.api_keys import ApiKeyRepository

    matched = ApiKeyRepository().authenticate(db, token)
    if matched is None:
        raise DomainError(
            DomainErrorCode.INVALID_API_KEY,
            "invalid x-api-key",
            status_code=401,
        )
    owner = db.get(User, matched.owner_user_id)
    if owner is None or not owner.is_active:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "invalid x-api-key", status_code=401)
    ApiKeyRepository().touch_last_used(db, matched.id)
    db.commit()
    return matched, owner


def _capture_headers(request: Request) -> dict[str, str]:
    """仅捕获与协议展示相关的头部；不保存 Authorization / Cookie。"""
    allow = {"user-agent", "accept", "anthropic-version", "openai-beta"}
    return {key: value for key, value in request.headers.items() if key.lower() in allow}


def _create_task(
    db: Session,
    key: ApiKey,
    owner: User,
    protocol: InferenceProtocol,
    parsed: Any,
    body: bytes,
    headers: dict[str, str],
) -> RequestTask:
    task = _service.create_task(
        db,
        key=key,
        owner=owner,
        protocol=protocol,
        parsed=parsed,
        raw_body=body,
        headers=headers,
    )
    db.commit()
    return task


class _Outcome:
    __slots__ = ("draft", "task")

    def __init__(self, task: RequestTask, draft: ReplyDraft) -> None:
        self.task = task
        self.draft = draft


def _load_task(task_id: int) -> RequestTask | None:
    # 函数内导入：conftest 会重绑 app.core.db.SessionLocal，模块级快照会指向错误引擎。
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        return session.get(RequestTask, task_id)


def _finalize(task_id: int, state: TaskState) -> None:
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is not None:
            _service.finalize(session, task, state)
            session.commit()


def _cancel_disconnected(task_id: int) -> None:
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        _service.cancel_caller_disconnected(session, task_id)
        session.commit()


def _mark_responding(task_id: int) -> None:
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is not None:
            _service.mark_responding(session, task)
            session.commit()


async def _wait_for_reply(request: Request, task_id: int, deadline_at: Any) -> _Outcome | None:
    """等待人工回复；返回 None 表示调用方已断开（任务已取消）。"""
    while True:
        if await request.is_disconnected():
            await run_in_threadpool(_cancel_disconnected, task_id)
            return None
        row = await run_in_threadpool(_load_task, task_id)
        if row is None:
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "任务不存在",
                status_code=500,
            )
        state = row.state
        if state is TaskState.RESPONSE_READY and row.response_payload_json:
            draft = ReplyDraft.model_validate_json(row.response_payload_json)
            return _Outcome(row, draft)
        if state is TaskState.TIMED_OUT:
            raise DomainError(
                DomainErrorCode.REQUEST_TIMEOUT,
                "The request timed out while waiting for a reply.",
                status_code=504,
            )
        if state is TaskState.CANCELLED:
            # 已被其他路径取消：不再产生响应。
            await run_in_threadpool(_cancel_disconnected, task_id)
            return None
        if state is TaskState.FAILED:
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "The server had an error while processing the request.",
                status_code=500,
            )
        if deadline_at is not None and _now() > deadline_at:
            await run_in_threadpool(_finalize, task_id, TaskState.TIMED_OUT)
            raise DomainError(
                DomainErrorCode.REQUEST_TIMEOUT,
                "The request timed out while waiting for a reply.",
                status_code=504,
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _now() -> Any:
    from ..core.time import utc_now

    return utc_now()


async def _stream(
    task: RequestTask,
    draft: ReplyDraft,
    frames: Callable[[], Any],
    error_frame: Callable[[str], ServerSentEvent],
) -> AsyncIterator[ServerSentEvent]:
    """伪流式输出：先提交完整结果，再逐帧输出（AGENTS.md 产品边界）。"""
    await run_in_threadpool(_mark_responding, task.id)
    try:
        for frame in frames():
            yield frame
        await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
    except asyncio.CancelledError:
        # 客户端断开：原子取消并释放名额。
        await run_in_threadpool(_cancel_disconnected, task.id)
        raise
    except (RuntimeError, ValueError, TypeError, AttributeError):
        yield error_frame("The server had an error while streaming the response.")
        await run_in_threadpool(_finalize, task.id, TaskState.FAILED)


def _json_response(payload: dict[str, Any], *, request_id: str) -> JSONResponse:
    headers = {}
    if request_id:
        headers["request-id"] = request_id
    return JSONResponse(payload, headers=headers)


async def _handle(
    request: Request,
    protocol: InferenceProtocol,
    parse: Callable[[bytes], Any],
    key: ApiKey,
    owner: User,
    db: Session,
) -> _Outcome | None:
    """建任务并等待人工回复；返回 None 表示调用方已断开（任务已取消）。"""
    body = await request.body()
    parsed = parse(body)
    headers = _capture_headers(request)
    task = await run_in_threadpool(_create_task, db, key, owner, protocol, parsed, body, headers)
    return await _wait_for_reply(request, task.id, task.human_deadline_at)


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    key, owner = key_owner

    async def run() -> Any:
        outcome = await _handle(
            request,
            InferenceProtocol.OPENAI_CHAT,
            chat_protocol.parse_request,
            key,
            owner,
            db,
        )
        if outcome is None:
            return JSONResponse(status_code=499, content={})
        task = outcome.task
        model = task.requested_model
        if not task.stream_requested:
            await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
            return _json_response(
                chat_protocol.render_response(model, outcome.draft),
                request_id="",
            )
        return EventSourceResponse(
            _stream(
                task,
                outcome.draft,
                lambda: chat_protocol.stream_frames(model, outcome.draft),
                chat_protocol.stream_error_frame,
            )
        )

    return await run()


@router.post("/responses")
async def create_response(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    key, owner = key_owner

    async def run() -> Any:
        outcome = await _handle(
            request,
            InferenceProtocol.OPENAI_RESPONSES,
            responses_protocol.parse_request,
            key,
            owner,
            db,
        )
        if outcome is None:
            return JSONResponse(status_code=499, content={})
        task = outcome.task
        model = task.requested_model
        response_id = task.response_public_id or ""
        if not task.stream_requested:
            await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
            return _json_response(
                responses_protocol.render_response(model, response_id, outcome.draft),
                request_id="",
            )
        return EventSourceResponse(
            _stream(
                task,
                outcome.draft,
                lambda: responses_protocol.stream_events(model, response_id, outcome.draft),
                lambda message: responses_protocol.stream_error_event(response_id, model, message),
            )
        )

    return await run()


@router.post("/messages")
async def anthropic_messages(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(_require_api_key_anthropic),
    db: Session = Depends(get_db),
):
    key, owner = key_owner

    async def run() -> Any:
        outcome = await _handle(
            request,
            InferenceProtocol.ANTHROPIC_MESSAGES,
            anthropic_protocol.parse_request,
            key,
            owner,
            db,
        )
        if outcome is None:
            return JSONResponse(status_code=499, content={})
        task = outcome.task
        model = task.requested_model
        if not task.stream_requested:
            await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
            return _json_response(
                anthropic_protocol.render_response(model, outcome.draft),
                request_id=get_request_id() or "",
            )
        return EventSourceResponse(
            _stream(
                task,
                outcome.draft,
                lambda: anthropic_protocol.stream_events(model, outcome.draft),
                lambda _message: anthropic_protocol.stream_error_event(),
            )
        )

    return await run()

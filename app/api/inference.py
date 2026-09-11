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
from ..core.logging import get_request_id, log_event
from ..domain.enums import InferenceProtocol, ReplyStrategy, TaskState
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.tokens import TokenSnapshot
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
            "Authentication required",
            status_code=401,
        )
    from ..repositories.api_keys import ApiKeyRepository

    matched = ApiKeyRepository().authenticate(db, token)
    if matched is None:
        raise DomainError(
            DomainErrorCode.INVALID_API_KEY,
            "Invalid API key",
            status_code=401,
        )
    owner = db.get(User, matched.owner_user_id)
    if owner is None or not owner.is_active:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "Invalid API key", status_code=401)
    ApiKeyRepository().touch_last_used(db, matched.id)
    db.commit()
    return matched, owner


def _capture_headers(request: Request) -> dict[str, str]:
    """仅捕获与协议展示相关的头部；不保存 Authorization / Cookie。"""
    allow = {
        "user-agent",
        "accept",
        "accept-language",
        "content-type",
        "anthropic-version",
        "openai-beta",
    }
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


def _finalize(task_id: int, state: TaskState, public_error: DomainErrorCode | None = None) -> None:
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is not None:
            # 超时终态只允许从等待/转发中推进：人工先到（RESPONSE_READY）或
            # 已开始输出（RESPONDING）的任务不被覆盖。
            allowed = (
                {TaskState.WAITING_HUMAN, TaskState.FORWARDING_LLM}
                if state is TaskState.TIMED_OUT
                else None
            )
            _service.finalize(
                session, task, state, allowed_sources=allowed, public_error=public_error
            )
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


async def _reject_unsupported_forward(task_id: int, error: str | None) -> None:
    if error != DomainErrorCode.UNSUPPORTED_PARAMETER.value:
        return
    await run_in_threadpool(
        _finalize, task_id, TaskState.FAILED, DomainErrorCode.UNSUPPORTED_PARAMETER
    )
    raise DomainError(
        DomainErrorCode.UNSUPPORTED_PARAMETER,
        "The request contains parameters that are not supported by this model.",
        status_code=400,
    )


async def _run_direct_forward(task_id: int, *, stream: bool = False) -> None:
    """llm 策略：立即转发；不支持的参数返回 400，其余失败返回通用 500。

    stream=True 时上游以 SSE 流式接收（增量聚合后仍按完整结果原子落库，
    再由等待循环进入伪流式输出）。
    失败时先推进 FAILED 并释放名额；字段矩阵拒绝保留协议错误类别，
    上游和基础设施错误不向调用方暴露内部消息。
    """
    import asyncio as _asyncio

    from ..core.db import SessionLocal
    from ..services.llm_forward_service import LlmForwardService

    def _forward() -> tuple[bool, str | None]:
        with SessionLocal() as session:
            task = session.get(RequestTask, task_id)
            if task is None:
                return False, "task_missing"
            service = LlmForwardService()
            if stream:
                accepted, _chunks, error = _asyncio.run(
                    service.forward_stream(session, task, reason="direct")
                )
            else:
                accepted, _draft, error = _asyncio.run(
                    service.forward(session, task, reason="direct")
                )
            return accepted, error

    try:
        accepted, error = await run_in_threadpool(_forward)
    except Exception:  # noqa: BLE001  # 转发基础设施异常按失败终态
        accepted, error = False, "exception"
    if not accepted:
        log_event(
            "warning",
            "inference.forward_failed",
            "llm 策略直接转发失败",
            task_id=task_id,
            error_code=error or "unknown",
        )
        await _reject_unsupported_forward(task_id, error)
        await run_in_threadpool(_finalize, task_id, TaskState.FAILED)


async def _run_fallback(task_id: int) -> _Outcome | None:
    """human_fallback_llm 超时转发：成功返回 Outcome，失败返回 None（走终态）。

    转发失败不重试；字段矩阵拒绝返回 400，其余失败返回通用超时错误。
    """
    from ..core.db import SessionLocal
    from ..services.llm_forward_service import LlmForwardService

    def _forward() -> tuple[bool, str | None]:
        with SessionLocal() as session:
            task = session.get(RequestTask, task_id)
            if task is None:
                return False, "task_missing"
            import asyncio as _asyncio

            service = LlmForwardService()
            accepted, _draft, error = _asyncio.run(
                service.forward(session, task, reason="human_timeout")
            )
            return accepted, error

    try:
        accepted, error = await run_in_threadpool(_forward)
    except Exception:  # noqa: BLE001  # fallback 失败按超时终态处理
        return None
    if not accepted:
        log_event(
            "warning",
            "inference.fallback_failed",
            "human_fallback_llm 转发未接受",
            task_id=task_id,
            error_code=error or "unknown",
        )
        await _reject_unsupported_forward(task_id, error)
        return None
    row = await run_in_threadpool(_load_task, task_id)
    if row is None or not row.response_payload_json:
        return None
    draft = ReplyDraft.model_validate_json(row.response_payload_json)
    return _Outcome(row, draft)


async def _wait_for_reply(request: Request, task_id: int, deadline_at: Any) -> _Outcome | None:
    """等待人工回复；返回 None 表示调用方已断开（任务已取消）。"""
    while True:
        if await request.is_disconnected():
            await run_in_threadpool(_cancel_disconnected, task_id)
            return None
        row = await run_in_threadpool(_load_task, task_id)
        if row is None:
            # 对外只返回通用 500；中文上下文仅本地日志可见（§16.3）。
            log_event(
                "error",
                "inference.task_missing",
                "等待人工回复期间任务行不存在",
                task_id=task_id,
            )
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "Internal server error.",
                status_code=500,
            )
        state = row.state
        if state is TaskState.RESPONSE_READY and row.response_payload_json:
            draft = ReplyDraft.model_validate_json(row.response_payload_json)
            return _Outcome(row, draft)
        if state is TaskState.TIMED_OUT:
            # 通用超时错误，不暴露人工等待细节（§16.3）。
            log_event(
                "info",
                "inference.human_timeout",
                "等待人工回复超时",
                task_id=task_id,
            )
            raise DomainError(
                DomainErrorCode.REQUEST_TIMEOUT,
                "Request timed out.",
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
            # human_fallback_llm：超时后原子声明一次转发权；成功则继续
            # 伪流式输出，失败（声明丢失 / 上游错误）走终态。
            if row.reply_strategy_snapshot is ReplyStrategy.HUMAN_FALLBACK_LLM:
                fallback = await _run_fallback(task_id)
                if fallback is not None:
                    return fallback
            # 竞态防御：fallback 声明失败可能是因为人工恰在超时判定后抢先
            # 提交（任务已 RESPONSE_READY）。终态化前重读一次状态，人工已
            # 到则返回人工回复，不覆盖。
            fresh = await run_in_threadpool(_load_task, task_id)
            if (
                fresh is not None
                and fresh.state is TaskState.RESPONSE_READY
                and fresh.response_payload_json
            ):
                draft = ReplyDraft.model_validate_json(fresh.response_payload_json)
                return _Outcome(fresh, draft)
            await run_in_threadpool(_finalize, task_id, TaskState.TIMED_OUT)
            # 通用超时错误，不暴露人工等待细节（§16.3）。
            log_event(
                "info",
                "inference.human_timeout",
                "等待人工回复超时",
                task_id=task_id,
            )
            raise DomainError(
                DomainErrorCode.REQUEST_TIMEOUT,
                "Request timed out.",
                status_code=504,
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _now() -> Any:
    from ..core.time import utc_now

    return utc_now()


def _snapshot(task: RequestTask, draft: ReplyDraft) -> TokenSnapshot:
    import json as _json

    try:
        normalized = _json.loads(task.normalized_request_json or "{}")
    except (ValueError, TypeError):
        normalized = {}
    context = normalized.get("context") if isinstance(normalized, dict) else None
    instructions = normalized.get("instructions") if isinstance(normalized, dict) else None
    return TokenSnapshot.build(
        context=context if isinstance(context, list) else [],
        instructions=instructions if isinstance(instructions, str) else None,
        reasoning=draft.reasoning,
        tool_calls=[
            {"name": call.name, "arguments": call.arguments, "id": call.id}
            for call in draft.tool_calls
        ],
        final_text=draft.final_text,
    )


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


def _json_response(
    payload: dict[str, Any],
    *,
    request_id: str,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers: dict[str, str] = {}
    if request_id:
        headers["request-id"] = request_id
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(payload, headers=headers)


async def _handle(
    request: Request,
    protocol: InferenceProtocol,
    parse: Callable[[bytes], Any],
    key: ApiKey,
    owner: User,
    db: Session,
) -> _Outcome | None:
    """建任务并等待回复；返回 None 表示调用方已断开（任务已取消）。

    - human：等待人工回复直到超时。
    - llm：创建后立即转发真实 LLM（不经人工等待）。
    - human_fallback_llm：先等待人工；超时后由等待循环触发一次 fallback。
    """
    body = await request.body()
    parsed = parse(body)
    headers = _capture_headers(request)
    task = await run_in_threadpool(_create_task, db, key, owner, protocol, parsed, body, headers)
    if key.reply_strategy is ReplyStrategy.LLM:
        # 调用方请求流式时，上游同样以 SSE 接收（增量聚合后原子落库）。
        await _run_direct_forward(task.id, stream=parsed.stream)
    return await _wait_for_reply(request, task.id, task.human_deadline_at)


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    key, owner = key_owner
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
    snap = _snapshot(task, outcome.draft)
    usage = {
        "prompt_tokens": snap.input_tokens,
        "completion_tokens": snap.output_tokens,
        "total_tokens": snap.total_tokens,
    }
    # stream_options.include_usage：finish 帧后追加带 usage 的空 choices 帧。
    # 任务已创建成功；直接从原始请求体读取该网关透明字段。
    include_usage = False
    if task.stream_requested:
        import json as _json

        try:
            raw_options = _json.loads(task.raw_payload_json or "{}").get("stream_options")
            include_usage = isinstance(raw_options, dict) and bool(raw_options.get("include_usage"))
        except (ValueError, TypeError):
            include_usage = False
    if not task.stream_requested:
        await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
        return _json_response(
            chat_protocol.render_response(model, outcome.draft, usage=usage),
            request_id="",
        )
    return EventSourceResponse(
        _stream(
            task,
            outcome.draft,
            lambda: chat_protocol.stream_frames(
                model, outcome.draft, usage=usage, include_usage=include_usage
            ),
            chat_protocol.stream_error_frame,
        )
    )


@router.post("/responses")
async def create_response(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    key, owner = key_owner
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
    snap = _snapshot(task, outcome.draft)
    usage = {
        "input_tokens": snap.input_tokens,
        "output_tokens": snap.output_tokens,
        "total_tokens": snap.total_tokens,
    }
    if not task.stream_requested:
        await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
        return _json_response(
            responses_protocol.render_response(model, response_id, outcome.draft, usage=usage),
            request_id="",
        )
    return EventSourceResponse(
        _stream(
            task,
            outcome.draft,
            lambda: responses_protocol.stream_events(
                model, response_id, outcome.draft, usage=usage
            ),
            lambda message: responses_protocol.stream_error_event(response_id, model, message),
        )
    )


@router.post("/messages")
async def anthropic_messages(
    request: Request,
    key_owner: tuple[ApiKey, User] = Depends(_require_api_key_anthropic),
    db: Session = Depends(get_db),
):
    key, owner = key_owner
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
    snap = _snapshot(task, outcome.draft)
    usage = {"input_tokens": snap.input_tokens, "output_tokens": snap.output_tokens}
    if not task.stream_requested:
        await run_in_threadpool(_finalize, task.id, TaskState.COMPLETED)
        # Anthropic SDK 客户端期望响应头回显请求的 anthropic-version。
        return _json_response(
            anthropic_protocol.render_response(model, outcome.draft, usage=usage),
            request_id=get_request_id() or "",
            extra_headers={"anthropic-version": request.headers.get("anthropic-version", "")},
        )
    return EventSourceResponse(
        _stream(
            task,
            outcome.draft,
            lambda: anthropic_protocol.stream_events(model, outcome.draft, usage=usage),
            lambda _message: anthropic_protocol.stream_error_event(),
        )
    )

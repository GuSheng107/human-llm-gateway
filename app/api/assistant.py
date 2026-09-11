"""Web 小助手 API（docs/API_CONTRACT.md §10，M8-A）。

会话与消息严格按 owner 隔离；页面上下文经封闭 schema + 正则擦洗双层
过滤后落库（app/services/assistant/redaction.py）。回复支持同步 JSON 与
SSE 流式两种取回方式（共用同一条落库路径）。第一阶段只生成文本与
建议，不提供可执行系统工具。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.errors import DomainError
from ..repositories.models import AssistantMessage, AssistantSession, User
from ..services.assistant.service import AssistantService
from .common import StrictModel
from .deps import require_current_user
from .errors import get_request_id

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_service = AssistantService()


# ----------------------------------------------------------------------
# 请求模型
# ----------------------------------------------------------------------


class ToolCallSnapshot(StrictModel):
    id: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class UnsavedEditSnapshot(StrictModel):
    reasoning: str | None = Field(default=None, max_length=20000)
    final_text: str | None = Field(default=None, max_length=40000)
    tool_calls: list[ToolCallSnapshot] = Field(default_factory=list, max_length=20)


class PageContextSnapshot(StrictModel):
    """封闭 schema：未知字段 422/400 拒收（allowlist 哲学）。"""

    route: str = Field(default="", max_length=255)
    feature: str = Field(min_length=1, max_length=100)
    resource: dict[str, str] = Field(default_factory=dict)
    unsaved_edit: UnsavedEditSnapshot | None = None
    context_version: int = Field(default=1, ge=1, le=10_000)


class SessionCreate(StrictModel):
    title: str = Field(default="新会话", max_length=255)
    llm_config_id: int = Field(ge=1)


class SessionPatch(StrictModel):
    title: str | None = Field(default=None, max_length=255)


class MessageSend(StrictModel):
    text: str = Field(min_length=1, max_length=20000)
    page_context: PageContextSnapshot | None = None


# ----------------------------------------------------------------------
# 视图模型
# ----------------------------------------------------------------------


class ToolCallView(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class UnsavedEditView(BaseModel):
    reasoning: str | None
    final_text: str | None
    tool_calls: list[ToolCallView]


class ContextView(BaseModel):
    route: str
    feature: str
    resource: dict[str, str]
    unsaved_edit: UnsavedEditView | None = None
    context_version: int


class MessageView(BaseModel):
    id: str
    role: str
    kind: str
    text: str
    page_context: ContextView | None
    upstream_metadata: dict[str, Any] | None
    trace_id: str | None = None
    error_code: str | None = None
    created_at: str


class SessionView(BaseModel):
    id: str
    title: str
    llm_config_id: str | None
    last_message_at: str | None
    created_at: str


class SessionUsageView(BaseModel):
    estimated_tokens: int
    limit_tokens: int
    ratio: float
    message_count: int
    compressing: bool = False


class SessionDetailView(SessionView):
    messages: list[MessageView]
    usage: SessionUsageView


# ----------------------------------------------------------------------
# 转换
# ----------------------------------------------------------------------


def _context_view(row: AssistantMessage) -> ContextView | None:
    if not row.page_context_json:
        return None
    try:
        context = json.loads(row.page_context_json)
    except (ValueError, TypeError):
        return None
    edit_raw = context.get("unsaved_edit")
    edit = None
    if isinstance(edit_raw, dict):
        edit = UnsavedEditView(
            reasoning=edit_raw.get("reasoning"),
            final_text=edit_raw.get("final_text"),
            tool_calls=[
                ToolCallView(
                    id=call.get("id", ""),
                    name=call.get("name", ""),
                    arguments=call.get("arguments") or {},
                )
                for call in edit_raw.get("tool_calls") or []
            ],
        )
    return ContextView(
        route=context.get("route", ""),
        feature=context.get("feature", ""),
        resource=context.get("resource") or {},
        unsaved_edit=edit,
        context_version=context.get("context_version", 1),
    )


def _message_view(row: AssistantMessage) -> MessageView:
    try:
        text = json.loads(row.content_json).get("text", "")
    except (ValueError, TypeError):
        text = ""
    metadata = None
    trace_id: str | None = None
    error_code: str | None = None
    if row.upstream_metadata_json:
        try:
            parsed = json.loads(row.upstream_metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
                tid = parsed.get("trace_id")
                if isinstance(tid, str) and tid:
                    trace_id = tid
                err = parsed.get("error_code")
                if isinstance(err, str) and err:
                    error_code = err
        except (ValueError, TypeError):
            metadata = None
    role_value = row.role.value if hasattr(row.role, "value") else str(row.role)
    kind_value = (
        (row.kind.value if hasattr(row.kind, "value") else str(row.kind))
        if row.kind is not None
        else "normal"
    )
    return MessageView(
        id=str(row.id),
        role=role_value,
        kind=kind_value,
        text=text,
        page_context=_context_view(row),
        upstream_metadata=metadata,
        trace_id=trace_id,
        error_code=error_code,
        created_at=iso_utc(row.created_at) or "",
    )


def _session_view(
    row: AssistantSession,
    *,
    messages: list[MessageView] | None = None,
    usage: SessionUsageView | None = None,
) -> Any:
    base = SessionView(
        id=str(row.id),
        title=row.title,
        llm_config_id=str(row.llm_config_id) if row.llm_config_id else None,
        last_message_at=iso_utc(row.last_message_at),
        created_at=iso_utc(row.created_at) or "",
    )
    if messages is None and usage is None:
        return base
    payload = base.model_dump()
    if messages is not None:
        payload["messages"] = messages
    if usage is not None:
        payload["usage"] = usage
    return SessionDetailView(**payload)


# ----------------------------------------------------------------------
# 端点
# ----------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionView])
def list_sessions(
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> list[SessionView]:
    rows = _service.list_sessions(db, user=user)
    return [_session_view(row) for row in rows]


@router.post("/sessions", response_model=SessionView, status_code=201)
def create_session(
    payload: SessionCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> SessionView:
    row = _service.create_session(
        db, user=user, title=payload.title, llm_config_id=payload.llm_config_id
    )
    db.commit()
    db.refresh(row)
    return _session_view(row)


@router.patch("/sessions/{session_id}", response_model=SessionView)
def patch_session(
    session_id: int,
    payload: SessionPatch,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> SessionView:
    """局部更新会话：仅支持标题。"""
    row = _service.update_session(db, user=user, session_id=session_id, title=payload.title)
    db.commit()
    db.refresh(row)
    return _session_view(row)


@router.get("/sessions/{session_id}", response_model=SessionDetailView)
def get_session(
    session_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> SessionDetailView:
    session_row = _service.get_session(db, user=user, session_id=session_id)
    messages = _service.list_messages(db, session_row=session_row)
    usage = _service.compute_usage(db, session_row=session_row, messages=messages)
    return _session_view(
        session_row,
        messages=[_message_view(m) for m in messages],
        usage=usage,
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    session_row = _service.get_session(db, user=user, session_id=session_id)
    _service.delete_session(db, user=user, session_id=session_row.id)
    db.commit()
    return Response(status_code=204)


def _context_raw_from_payload(payload: MessageSend) -> dict[str, Any] | None:
    if payload.page_context is None:
        return None
    edit = payload.page_context.unsaved_edit
    return {
        "route": payload.page_context.route,
        "feature": payload.page_context.feature,
        "resource": payload.page_context.resource,
        "context_version": payload.page_context.context_version,
        "unsaved_edit": edit.model_dump() if edit is not None else None,
    }


@router.post("/sessions/{session_id}/messages", response_model=MessageView, status_code=201)
async def send_message(
    session_id: int,
    payload: MessageSend,
    request: Request,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> MessageView:
    session_row = _service.get_session(db, user=user, session_id=session_id)
    context_raw = _context_raw_from_payload(payload)
    reply = await _service.send_message(
        db,
        user=user,
        session_row=session_row,
        text=payload.text,
        page_context_raw=context_raw,
        trace_id=get_request_id(request),
    )
    db.commit()
    db.refresh(reply)
    return _message_view(reply)


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    payload: MessageSend,
    request: Request,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE 流式发送：data 事件依次为 delta / done / error。

    会话与上下文校验在响应开始前完成（404/400 走统一错误结构）；
    流中的 DomainError 转为 error 事件（HTTP 已 200，无法改状态码）。
    每个事件都携带 ``trace_id``（来自中间件注入），便于用户与后端日志对账。
    """
    trace_id = get_request_id(request)
    session_row = _service.get_session(db, user=user, session_id=session_id)
    context_raw = _context_raw_from_payload(payload)

    async def event_stream():
        try:
            async for event in _service.stream_message(
                db,
                user=user,
                session_row=session_row,
                text=payload.text,
                page_context_raw=context_raw,
                trace_id=trace_id,
            ):
                payload_obj = dict(event)
                payload_obj["trace_id"] = trace_id
                yield f"data: {json.dumps(payload_obj, ensure_ascii=False)}\n\n"
        except DomainError as exc:
            error_event = {
                "type": "error",
                "code": str(exc.code),
                "message": exc.message or str(exc.code),
                "trace_id": trace_id,
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

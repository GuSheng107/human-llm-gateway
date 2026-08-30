"""Web 小助手 API（docs/API_CONTRACT.md §10，M8-A）。

会话与消息严格按 owner 隔离；页面上下文经封闭 schema + 正则擦洗双层
过滤后落库（app/services/assistant/redaction.py）。第一阶段只生成文本
与建议，不提供可执行系统工具。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..repositories.models import AssistantMessage, AssistantSession, User
from ..services.assistant.service import AssistantService
from .common import StrictModel
from .deps import require_current_user

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
    llm_config_id: int | None = None


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
    text: str
    page_context: ContextView | None
    upstream_metadata: dict[str, Any] | None
    created_at: str


class SessionView(BaseModel):
    id: str
    title: str
    llm_config_id: str | None
    last_message_at: str | None
    created_at: str


class SessionDetailView(SessionView):
    messages: list[MessageView]


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
    if row.upstream_metadata_json:
        try:
            parsed = json.loads(row.upstream_metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
        except (ValueError, TypeError):
            metadata = None
    return MessageView(
        id=str(row.id),
        role=row.role.value,
        text=text,
        page_context=_context_view(row),
        upstream_metadata=metadata,
        created_at=iso_utc(row.created_at) or "",
    )


def _session_view(row: AssistantSession, *, messages: list[MessageView] | None = None) -> Any:
    base = SessionView(
        id=str(row.id),
        title=row.title,
        llm_config_id=str(row.llm_config_id) if row.llm_config_id else None,
        last_message_at=iso_utc(row.last_message_at),
        created_at=iso_utc(row.created_at) or "",
    )
    if messages is None:
        return base
    return SessionDetailView(**base.model_dump(), messages=messages)


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


@router.get("/sessions/{session_id}", response_model=SessionDetailView)
def get_session(
    session_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> SessionDetailView:
    session_row = _service.get_session(db, user=user, session_id=session_id)
    messages = _service.list_messages(db, session_row=session_row)
    return _session_view(session_row, messages=[_message_view(m) for m in messages])


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


@router.post("/sessions/{session_id}/messages", response_model=MessageView, status_code=201)
async def send_message(
    session_id: int,
    payload: MessageSend,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> MessageView:
    session_row = _service.get_session(db, user=user, session_id=session_id)
    context_raw = None
    if payload.page_context is not None:
        edit = payload.page_context.unsaved_edit
        context_raw = {
            "route": payload.page_context.route,
            "feature": payload.page_context.feature,
            "resource": payload.page_context.resource,
            "context_version": payload.page_context.context_version,
            "unsaved_edit": edit.model_dump() if edit is not None else None,
        }
    reply = await _service.send_message(
        db,
        user=user,
        session_row=session_row,
        text=payload.text,
        page_context_raw=context_raw,
    )
    db.commit()
    db.refresh(reply)
    return _message_view(reply)

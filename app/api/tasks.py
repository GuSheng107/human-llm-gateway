"""任务工作�?API（docs/API_CONTRACT.md §9）：任务详情、事件时间线、草稿与原子提交�?

LLM 草稿生成（POST /api/tasks/{id}/drafts/generate）属�?M7，本阶段不提供�?
管理员对草稿与回复写接口只读：归属校验、状态校验与禁写均在 TaskService 内完成�?
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.dsl import is_empty_draft
from ..domain.enums import TaskState, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft, ReplyToolCall
from ..repositories.models import FakeModel, RequestTask, TaskDraft, TaskEvent, User
from ..services.delivery_service import DeliveryService
from ..services.task_service import TaskService, draft_from_row
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_service = TaskService()


# ------------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------------


class ToolCallInput(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReplyDraftInput(StrictModel):
    reasoning: str | None = Field(default=None, max_length=20000)
    tool_calls: list[ToolCallInput] = Field(default_factory=list, max_length=20)
    final_text: str | None = Field(default=None, max_length=40000)


class ReplySubmitInput(StrictModel):
    reasoning: str | None = Field(default=None, max_length=20000)
    tool_calls: list[ToolCallInput] = Field(default_factory=list, max_length=20)
    final_text: str | None = Field(default=None, max_length=40000)
    source_draft_id: int | None = None


class DraftGenerateInput(StrictModel):
    llm_config_id: int = Field(ge=1)


# ------------------------------------------------------------------
# 视图模型
# ------------------------------------------------------------------


class ToolCallView(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class DraftView(BaseModel):
    id: str
    source: str
    state: str
    reasoning: str | None
    tool_calls: list[ToolCallView]
    final_text: str | None
    created_at: str
    updated_at: str


class EventView(BaseModel):
    id: str
    event_type: str
    actor_type: str
    actor_user_id: str | None
    request_id: str | None
    payload: dict[str, Any] | None
    created_at: str


class TaskItem(BaseModel):
    id: str
    public_id: str
    requested_model: str
    fake_model_name: str
    protocol: str
    state: str
    reply_strategy: str
    delivery_mode: str
    api_key_prefix: str
    stream_requested: bool
    has_tools: bool
    response_id: str | None
    human_deadline_at: str | None
    created_at: str
    completed_at: str | None
    owner_user_id: str | None = None
    owner_username: str | None = None


class TaskPage(BaseModel):
    items: list[TaskItem]
    page: int
    page_size: int
    total: int


class ReplyDraftView(BaseModel):
    reasoning: str | None
    tool_calls: list[ToolCallView]
    final_text: str | None


class TaskDetail(TaskItem):
    is_owner: bool
    can_edit: bool
    prompt_text: str
    tool_names: list[str]
    raw_request: dict[str, Any] | None
    previous_task_id: str | None
    drafts: list[DraftView]
    active_draft_id: str | None
    result_draft: ReplyDraftView | None
    public_error_code: str | None
    cancel_reason_code: str | None
    events: list[EventView]
    events_total: int


class EventPage(BaseModel):
    items: list[EventView]
    page: int
    page_size: int
    total: int


class ReplyResultView(BaseModel):
    accepted: bool
    task_id: str
    state: str


# ------------------------------------------------------------------
# 转换
# ------------------------------------------------------------------


def _tool_call_view(call: ReplyToolCall) -> ToolCallView:
    return ToolCallView(id=call.id, name=call.name, arguments=call.arguments)


def _draft_view(row: TaskDraft) -> DraftView:
    draft = draft_from_row(row)
    return DraftView(
        id=str(row.id),
        source=row.source.value,
        state=row.state.value,
        reasoning=draft.reasoning,
        tool_calls=[_tool_call_view(c) for c in draft.tool_calls],
        final_text=draft.final_text,
        created_at=iso_utc(row.created_at) or "",
        updated_at=iso_utc(row.updated_at) or "",
    )


def _event_view(row: TaskEvent) -> EventView:
    payload: dict[str, Any] | None = None
    if row.payload_json:
        try:
            parsed = json.loads(row.payload_json)
            if isinstance(parsed, dict):
                payload = parsed
        except (ValueError, TypeError):
            payload = None
    return EventView(
        id=str(row.id),
        event_type=row.event_type.value,
        actor_type=row.actor_type.value,
        actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
        request_id=row.request_id,
        payload=payload,
        created_at=iso_utc(row.created_at) or "",
    )


def _is_owner(task: RequestTask, user: User) -> bool:
    return task.owner_user_id == user.id


def _summary(task: RequestTask) -> tuple[str, list[str]]:
    return DeliveryService._extract_request_summary(task)


def _batch_fake_model_names(session: Session, tasks: list[RequestTask]) -> dict[int, str]:
    """批量解析 FakeModel 名称；批量查询避免列�?N+1�?""
    ids = {t.fake_model_id for t in tasks if t.fake_model_id is not None}
    if not ids:
        return {}
    rows = session.execute(select(FakeModel).where(FakeModel.id.in_(ids))).scalars().all()
    return {row.id: row.model_id for row in rows}


def _batch_owner_usernames(session: Session, tasks: list[RequestTask]) -> dict[int, str]:
    """批量解析 owner username；管理员列表用，普通用户无 include_owner�?""
    ids = {t.owner_user_id for t in tasks}
    if not ids:
        return {}
    rows = session.execute(select(User).where(User.id.in_(ids))).scalars().all()
    return {row.id: row.username for row in rows}


def _item_view(
    session: Session,
    task: RequestTask,
    *,
    include_owner: bool = False,
    fake_model_names: dict[int, str] | None = None,
    owner_usernames: dict[int, str] | None = None,
) -> TaskItem:
    _prompt, tool_names = _summary(task)
    owner_username: str | None = None
    if include_owner and owner_usernames is not None:
        owner_username = owner_usernames.get(task.owner_user_id)
    elif include_owner:
        owner = session.get(User, task.owner_user_id)
        owner_username = owner.username if owner else None
    if fake_model_names is not None and task.fake_model_id is not None:
        fake_model_name = fake_model_names.get(task.fake_model_id, task.requested_model)
    else:
        fake_model_name = TaskService.fake_model_name(session, task)
    return TaskItem(
        id=str(task.id),
        public_id=task.public_id,
        requested_model=task.requested_model,
        fake_model_name=fake_model_name,
        protocol=task.protocol.value,
        state=task.state.value,
        reply_strategy=task.reply_strategy_snapshot.value,
        delivery_mode=task.delivery_mode_snapshot.value,
        api_key_prefix=task.api_key_prefix_snapshot,
        stream_requested=task.stream_requested,
        has_tools=bool(tool_names),
        response_id=task.response_public_id,
        human_deadline_at=iso_utc(task.human_deadline_at),
        created_at=iso_utc(task.created_at) or "",
        completed_at=iso_utc(task.completed_at),
        owner_user_id=str(task.owner_user_id) if include_owner else None,
        owner_username=owner_username,
    )


def _detail_view(session: Session, task: RequestTask, user: User) -> TaskDetail:
    is_owner = _is_owner(task, user)
    prompt, tool_names = _summary(task)
    item = _item_view(session, task, include_owner=user.role is UserRole.ADMIN)
    drafts: list[DraftView] = []
    active_draft_id: str | None = None
    result_draft: ReplyDraftView | None = None
    raw_request: dict[str, Any] | None = None
    if is_owner:
        draft_rows = _service.drafts(session, task=task)
        drafts = [_draft_view(row) for row in draft_rows]
        active = _service.active_draft(session, task=task)
        active_draft_id = str(active.id) if active else None
        result = _service.result_draft(task)
        if result is not None:
            result_draft = ReplyDraftView(
                reasoning=result.reasoning,
                tool_calls=[_tool_call_view(c) for c in result.tool_calls],
                final_text=result.final_text,
            )
        try:
            raw_request = json.loads(task.raw_payload_json)
        except (ValueError, TypeError):
            raw_request = None
    events, events_total = _service.list_events(session, task=task, page=1, page_size=50)
    previous_public_id = _service.repo.get_previous_public_id(session, task)
    return TaskDetail(
        **item.model_dump(),
        is_owner=is_owner,
        can_edit=(
            is_owner and task.state is TaskState.WAITING_HUMAN and user.role is not UserRole.ADMIN
        ),
        prompt_text=prompt if is_owner else (prompt[:200] if prompt else ""),
        tool_names=tool_names,
        raw_request=raw_request,
        previous_task_id=previous_public_id,
        drafts=drafts,
        active_draft_id=active_draft_id,
        result_draft=result_draft,
        public_error_code=task.public_error_code,
        cancel_reason_code=task.cancel_reason_code,
        events=[_event_view(row) for row in events],
        events_total=events_total,
    )


def _to_draft(payload: ReplyDraftInput | ReplySubmitInput) -> ReplyDraft:
    return ReplyDraft(
        reasoning=payload.reasoning,
        tool_calls=[
            ReplyToolCall(id=c.id, name=c.name, arguments=c.arguments) for c in payload.tool_calls
        ],
        final_text=payload.final_text,
    )


def _get_task(db: Session, task_id: int, user: User) -> RequestTask:
    return _service.get_owned_task(db, task_id, user)


# ------------------------------------------------------------------
# 端点
# ------------------------------------------------------------------


@router.get("", response_model=TaskPage)
def list_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    state: TaskState | None = Query(default=None),
    # 分段筛选：in_progress（进行中�? finished（completed�? failed（失�?超时+取消�?
    bucket: str | None = Query(default=None, pattern="^(in_progress|finished|failed)$"),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> TaskPage:
    bucket_states: dict[str, list[TaskState]] = {
        "in_progress": [
            TaskState.RECEIVED,
            TaskState.WAITING_HUMAN,
            TaskState.FORWARDING_LLM,
            TaskState.RESPONSE_READY,
            TaskState.RESPONDING,
        ],
        "finished": [TaskState.COMPLETED],
        "failed": [TaskState.FAILED, TaskState.TIMED_OUT, TaskState.CANCELLED],
    }
    rows, total = _service.list_tasks(
        db,
        user=user,
        page=page,
        page_size=page_size,
        search=search,
        state=state,
        states=bucket_states.get(bucket or "") if bucket else None,
    )
    include_owner = user.role is UserRole.ADMIN
    fake_model_names = _batch_fake_model_names(db, rows) if rows else {}
    owner_usernames = _batch_owner_usernames(db, rows) if include_owner and rows else {}
    return TaskPage(
        items=[
            _item_view(
                db,
                row,
                include_owner=include_owner,
                fake_model_names=fake_model_names,
                owner_usernames=owner_usernames,
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> TaskDetail:
    task = _get_task(db, task_id, user)
    return _detail_view(db, task, user)


@router.get("/{task_id}/events", response_model=EventPage)
def list_task_events(
    task_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> EventPage:
    task = _get_task(db, task_id, user)
    rows, total = _service.list_events(db, task=task, page=page, page_size=page_size)
    return EventPage(
        items=[_event_view(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/{task_id}/drafts", response_model=DraftView, status_code=201)
def save_draft(
    task_id: int,
    payload: ReplyDraftInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    task = _get_task(db, task_id, user)
    row = _service.save_draft(db, task=task, owner=user, draft=_to_draft(payload))
    db.commit()
    db.refresh(row)
    return _draft_view(row)


@router.patch("/{task_id}/drafts/{draft_id}", response_model=DraftView)
def update_draft(
    task_id: int,
    draft_id: int,
    payload: ReplyDraftInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    task = _get_task(db, task_id, user)
    row = _service.update_draft(
        db, task=task, owner=user, draft_id=draft_id, draft=_to_draft(payload)
    )
    db.commit()
    db.refresh(row)
    return _draft_view(row)


@router.delete("/{task_id}/drafts/{draft_id}", status_code=204)
def delete_draft(
    task_id: int,
    draft_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    task = _get_task(db, task_id, user)
    _service.delete_draft(db, task=task, owner=user, draft_id=draft_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{task_id}/drafts/generate", response_model=DraftView, status_code=201)
async def generate_draft(
    task_id: int,
    payload: DraftGenerateInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    """调用用户选定 LLM 配置生成持久化草稿（M7-B）�?

    仅同协议：Chat/Responses 任务必须�?openai_chat；Anthropic 任务
    必须�?anthropic。跨协议生成在后续阶段（字段矩阵）开放�?
    """
    from ..services.llm_draft_service import LlmDraftService

    task = _get_task(db, task_id, user)
    generator = LlmDraftService()
    row = await generator.generate(db, task=task, owner=user, llm_config_id=payload.llm_config_id)
    db.commit()
    db.refresh(row)
    return _draft_view(row)


@router.post("/{task_id}/reply", response_model=ReplyResultView, status_code=201)
def submit_reply(
    task_id: int,
    payload: ReplySubmitInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ReplyResultView:
    task = _get_task(db, task_id, user)
    draft = _to_draft(payload)
    if is_empty_draft(draft):
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "回复内容不能为空", status_code=422)
    accepted = _service.submit_reply(
        db,
        task=task,
        owner=user,
        draft=draft,
        source_draft_id=payload.source_draft_id,
    )
    db.commit()
    if not accepted:
        raise DomainError(
            DomainErrorCode.CONFLICT,
            "任务已回复，晚到提交被拒�?,
            status_code=409,
            public_code="task_already_resolved",
        )
    db.refresh(task)
    return ReplyResultView(accepted=True, task_id=str(task.id), state=task.state.value)

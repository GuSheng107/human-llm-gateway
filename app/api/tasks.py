"""任务工作台 API（docs/API_CONTRACT.md §9）：请求视图、草稿、原子提交与工具告警。

管理员对草稿与回复写接口只读：归属校验、状态校验与禁写均在 TaskService 内完成。
请求视图（request-view）只投影本次请求：current_input / caller_system /
attached_context / attachments / caller_tools，不做网关会话历史语义。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import AuditAction, TaskState, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import (
    ReplyDraft,
    ReplyToolCall,
    is_empty_draft,
    normalize_generation_instruction,
)
from ..repositories.models import FakeModel, RequestTask, TaskDraft, TaskEvent, TaskInboxState, User
from ..services.caller_tool_service import catalog_for_task
from ..services.delivery_service import DeliveryService
from ..services.request_view_service import RequestViewService
from ..services.task_service import TaskService, draft_from_row
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_service = TaskService()
_view_service = RequestViewService()


# ------------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------------


class ToolCallInput(StrictModel):
    # 调用方 ID：校验非空 + 回复内唯一，服务端不重排（§8.1）。
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReplyDraftInput(StrictModel):
    reasoning: str | None = Field(default=None, max_length=20000)
    tool_calls: list[ToolCallInput] = Field(default_factory=list, max_length=20)
    final_text: str | None = Field(default=None, max_length=40000)


class DraftUpdateInput(ReplyDraftInput):
    """草稿部分更新：乐观锁强制要求 expected_version。"""

    expected_version: int = Field(ge=1)


class ReplySubmitInput(StrictModel):
    reasoning: str | None = Field(default=None, max_length=20000)
    tool_calls: list[ToolCallInput] = Field(default_factory=list, max_length=20)
    final_text: str | None = Field(default=None, max_length=40000)
    source_draft_id: int | None = None


class DraftGenerateInput(StrictModel):
    """LLM 草稿生成契约（§7.3）。

    - generation_instruction：一次生成操作的短生命周期引导（不落库、
      不进入最终回复；空白归一为 None，超限 400 generation_instruction_invalid）。
    - excluded_context_item_ids：附带上下文的稳定 ctx ID（current_input 不可
      排除；未知 ID 400 invalid_context_item）。
    - include_caller_system / include_attachments：调用方 system 与附件开关；
      附件无法承载时 422 attachment_not_supported，不静默降级。
    """

    llm_config_id: int = Field(ge=1)
    mode: Literal["reasoning", "reply", "both"] = "both"
    generation_instruction: str | None = Field(default=None)
    include_caller_system: bool = True
    excluded_context_item_ids: list[str] | None = Field(default=None, max_length=200)
    include_attachments: bool = True
    # mode=reply 时可携带人工已确认的思考链作为生成依据。
    reasoning_seed: str | None = Field(default=None, max_length=20000)


class ToolArgumentsGenerateInput(StrictModel):
    """指定工具参数生成契约（§7.4）。"""

    llm_config_id: int = Field(ge=1)
    generation_instruction: str | None = Field(default=None)
    include_caller_system: bool = True
    excluded_context_item_ids: list[str] | None = Field(default=None, max_length=200)
    include_attachments: bool = True
    current_arguments: dict[str, Any] = Field(default_factory=dict)


class AcknowledgeBody(StrictModel):
    pass


# ------------------------------------------------------------------
# 视图模型
# ------------------------------------------------------------------


class ToolCallView(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ToolDefinitionView(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, Any]
    source_type: str
    is_generatable: bool


class DraftView(BaseModel):
    id: str
    source: str
    state: str
    reasoning: str | None
    tool_calls: list[ToolCallView]
    final_text: str | None
    version: int
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
    api_key_name: str
    display_name: str
    stream_requested: bool
    has_tools: bool
    prompt_preview: str
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
    request_id: str
    is_owner: bool
    can_edit: bool
    prompt_text: str
    tool_definitions: list[ToolDefinitionView]
    # 旧客户端只需要名称；完整 Schema 以 tool_definitions 为准。
    tool_names: list[str] = Field(default_factory=list)
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


class InboxItem(BaseModel):
    id: str
    public_id: str
    requested_model: str
    fake_model_name: str
    protocol: str
    state: str
    human_deadline_at: str | None
    created_at: str
    prompt_preview: str
    api_key_name: str
    display_name: str
    has_tools: bool
    unread: bool
    seen_at: str | None
    last_seen_event_id: str | None
    owner_user_id: str | None = None
    owner_username: str | None = None


class InboxPage(BaseModel):
    items: list[InboxItem]
    waiting_count: int
    unread_count: int


class InboxSummary(BaseModel):
    unread_count: int
    waiting_count: int


# ----------------------------------------------------------------------
# 请求视图（RequestView，§6.2 / §7.1）
# ----------------------------------------------------------------------


class ContentBlockView(BaseModel):
    id: str
    type: str
    text: str | None = None
    text_length: int | None = None
    truncated: bool | None = None
    name: str | None = None
    media_type: str | None = None
    filename: str | None = None
    source: str | None = None
    url: str | None = None
    size_bytes: int | None = None
    previewable: bool | None = None
    call_id: str | None = None
    arguments: dict[str, Any] | None = None
    raw_type: str | None = None


class ContextItemView(BaseModel):
    id: str
    role: str
    blocks: list[ContentBlockView]
    text_length: int
    block_count: int


class CallerSystemView(BaseModel):
    items: list[ContextItemView]
    item_count: int
    character_count: int
    collapsed_by_default: bool


class CallerToolsView(BaseModel):
    definitions: list[ToolDefinitionView]
    choice: str
    required_name: str | None
    parallel_allowed: bool


class ToolCallWarningView(BaseModel):
    required: bool
    acknowledged: bool
    acknowledged_at: str | None


class RequestViewTask(BaseModel):
    id: str
    public_id: str
    request_id: str
    protocol: str
    requested_model: str
    state: str
    created_at: str | None
    deadline_at: str | None


class RequestViewResponse(BaseModel):
    task: RequestViewTask
    current_input: list[ContextItemView]
    caller_system: CallerSystemView
    attached_context: list[ContextItemView]
    attachments: list[ContentBlockView]
    caller_tools: CallerToolsView
    tool_call_warning: ToolCallWarningView
    raw_request_available: bool


class WarningAcknowledgeView(BaseModel):
    task_id: str
    acknowledged: bool
    acknowledged_at: str


class ToolArgumentsGenerateView(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    llm_config_id: str
    schema_valid: bool
    warnings: list[str]


# ----------------------------------------------------------------------
# 转换
# ----------------------------------------------------------------------


def _content_block_view(block: dict[str, Any]) -> ContentBlockView:
    return ContentBlockView(
        id=str(block.get("id") or ""),
        type=str(block.get("type") or "text"),
        text=block.get("text"),
        text_length=block.get("text_length"),
        truncated=block.get("truncated"),
        name=block.get("name"),
        media_type=block.get("media_type"),
        filename=block.get("filename"),
        source=block.get("source"),
        url=block.get("url"),
        size_bytes=block.get("size_bytes"),
        previewable=block.get("previewable") if block.get("previewable") is not None else None,
        call_id=block.get("call_id"),
        arguments=block.get("arguments"),
        raw_type=block.get("raw_type"),
    )


def _context_item_view(item: dict[str, Any]) -> ContextItemView:
    return ContextItemView(
        id=str(item.get("id") or ""),
        role=str(item.get("role") or "user"),
        blocks=[_content_block_view(block) for block in item.get("blocks") or []],
        text_length=int(item.get("text_length") or 0),
        block_count=int(item.get("block_count") or 0),
    )


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
        version=row.version,
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


def _tool_definitions(task: RequestTask) -> list[ToolDefinitionView]:
    catalog = catalog_for_task(task)
    return [
        ToolDefinitionView(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            source_type=tool.source_type,
            is_generatable=tool.is_generatable,
        )
        for tool in catalog.definitions
    ]


def _task_display_name(task: RequestTask) -> str:
    return f"{task.api_key_name_snapshot} · {task.requested_model}"


# 列表预览长度（字符）；取摘要尾部（Agent 提示词的提问在末尾）。
_PREVIEW_CAP = 160


def _preview_text(prompt: str) -> str:
    if not prompt:
        return ""
    if len(prompt) <= _PREVIEW_CAP:
        return prompt
    return f"…{prompt[-_PREVIEW_CAP:]}"


def _batch_fake_model_names(session: Session, tasks: list[RequestTask]) -> dict[int, str]:
    """批量解析 FakeModel 名称；批量查询避免列表 N+1。"""
    ids = {t.fake_model_id for t in tasks if t.fake_model_id is not None}
    if not ids:
        return {}
    rows = session.execute(select(FakeModel).where(FakeModel.id.in_(ids))).scalars().all()
    return {row.id: row.model_id for row in rows}


def _batch_owner_usernames(session: Session, tasks: list[RequestTask]) -> dict[int, str]:
    """批量解析 owner username；管理员列表用，普通用户无 include_owner。"""
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
        api_key_name=task.api_key_name_snapshot,
        display_name=_task_display_name(task),
        stream_requested=task.stream_requested,
        has_tools=bool(tool_names),
        prompt_preview=_preview_text(_prompt),
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
    events, events_total = _service.list_events(session, task=task, page=1, page_size=50)
    previous_public_id = _service.repo.get_previous_public_id(session, task)
    return TaskDetail(
        **item.model_dump(),
        request_id=task.request_id,
        is_owner=is_owner,
        can_edit=(
            is_owner and task.state is TaskState.WAITING_HUMAN and user.role is not UserRole.ADMIN
        ),
        # 所有者可查看完整提示词（Agent 海量上下文不截断）；非归属用户仅摘要前 200 字。
        prompt_text=prompt if is_owner else (prompt[:200] if prompt else ""),
        tool_definitions=_tool_definitions(task),
        tool_names=tool_names,
        raw_request=None,
        previous_task_id=previous_public_id,
        drafts=drafts,
        active_draft_id=active_draft_id,
        result_draft=result_draft,
        public_error_code=task.public_error_code,
        cancel_reason_code=task.cancel_reason_code,
        events=[_event_view(row) for row in events],
        events_total=events_total,
    )


def _to_draft(payload: ReplyDraftInput | ReplySubmitInput | DraftUpdateInput) -> ReplyDraft:
    return ReplyDraft(
        reasoning=payload.reasoning,
        tool_calls=[
            ReplyToolCall(id=c.id or "", name=c.name, arguments=c.arguments)
            for c in payload.tool_calls
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
    # 分段筛选：in_progress（进行中）、finished（completed）、failed（失败/超时+取消）
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


@router.get("/inbox", response_model=InboxPage)
def list_inbox(
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> InboxPage:
    """工作台收件箱：waiting_human 全量 + 未读位。

    必须在 /{task_id} 之前注册，否则静态路径被路径参数吞掉。
    上限 10 任务（MAX_ACTIVE_TASKS_PER_USER），无需分页。
    """
    rows = _service.repo.list_inbox(db, owner_user_id=user.id)
    task_ids = [row.id for row in rows]
    seen_map = _service.repo.list_seen_map(db, task_ids=task_ids)
    fake_model_names = _batch_fake_model_names(db, rows)
    include_owner = user.role is UserRole.ADMIN
    owner_usernames = _batch_owner_usernames(db, rows) if include_owner else {}
    unread_count = sum(1 for row in rows if row.id not in seen_map)
    return InboxPage(
        items=[
            _inbox_item(
                db,
                row,
                seen_map.get(row.id),
                include_owner=include_owner,
                fake_model_names=fake_model_names,
                owner_usernames=owner_usernames,
            )
            for row in rows
        ],
        waiting_count=len(rows),
        unread_count=unread_count,
    )


@router.get("/inbox-summary", response_model=InboxSummary)
def inbox_summary(
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> InboxSummary:
    waiting = _service.repo.count_waiting(db, owner_user_id=user.id)
    unread = _service.repo.count_unread(db, owner_user_id=user.id)
    return InboxSummary(unread_count=unread, waiting_count=waiting)


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> TaskDetail:
    task = _get_task(db, task_id, user)
    return _detail_view(db, task, user)


@router.get("/{task_id}/raw-request")
def get_task_raw_request(
    task_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """原始请求 JSON 按需加载（超大请求不随详情页传输）；仅所有者。"""
    task = _get_task(db, task_id, user)
    if task.owner_user_id != user.id and user.role is not UserRole.ADMIN:
        raise DomainError(DomainErrorCode.FORBIDDEN, "仅所有者可查看原始请求", status_code=403)
    try:
        raw = json.loads(task.raw_payload_json) if task.raw_payload_json else None
    except (ValueError, TypeError):
        raw = None
    return {"task_id": str(task.id), "raw_request": raw}


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
    payload: DraftUpdateInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    task = _get_task(db, task_id, user)
    row = _service.update_draft(
        db,
        task=task,
        owner=user,
        draft_id=draft_id,
        draft=_to_draft(payload),
        expected_version=payload.expected_version,
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


@router.post("/{task_id}/drafts/generate", response_model=DraftView)
async def generate_draft(
    task_id: int,
    payload: DraftGenerateInput,
    response: Response,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DraftView:
    """调用用户选定 LLM 生成草稿（契约见 DraftGenerateInput）。

    mode=reasoning/reply/both 分别只生成思考链、只生成回复（可携带
    reasoning_seed 作为人工已确认的思考依据）或两者。已存在未提交的
    LLM 草稿时按模式合并更新（此时返回 200）；否则新建（201）。
    """
    from ..services.llm_draft_service import LlmDraftService

    task = _get_task(db, task_id, user)
    generation_instruction = normalize_generation_instruction(payload.generation_instruction)
    merging = any(
        draft.source == "llm" and draft.state == "editing"
        for draft in _service.drafts(db, task=task)
    )
    generator = LlmDraftService()
    row = await generator.generate(
        db,
        task=task,
        owner=user,
        llm_config_id=payload.llm_config_id,
        mode=payload.mode,
        generation_instruction=generation_instruction,
        include_caller_system=payload.include_caller_system,
        excluded_context_item_ids=payload.excluded_context_item_ids,
        include_attachments=payload.include_attachments,
        reasoning_seed=payload.reasoning_seed,
    )
    db.commit()
    db.refresh(row)
    response.status_code = 200 if merging else 201
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
            "任务已回复，晚到提交被拒绝",
            status_code=409,
            public_code="task_already_resolved",
        )
    db.refresh(task)
    return ReplyResultView(accepted=True, task_id=str(task.id), state=task.state.value)


# ----------------------------------------------------------------------
# 工作台收件箱（M14）
# ----------------------------------------------------------------------


class SeenBody(StrictModel):
    last_seen_event_id: int | None = None


def _inbox_item(
    session: Session,
    task: RequestTask,
    seen: TaskInboxState | None,
    *,
    include_owner: bool,
    fake_model_names: dict[int, str],
    owner_usernames: dict[int, str],
) -> InboxItem:
    prompt, tool_names = _summary(task)
    fake_model_name = fake_model_names.get(task.fake_model_id or -1, task.requested_model)
    owner_username = owner_usernames.get(task.owner_user_id) if include_owner else None
    return InboxItem(
        id=str(task.id),
        public_id=task.public_id,
        requested_model=task.requested_model,
        fake_model_name=fake_model_name,
        protocol=task.protocol.value,
        state=task.state.value,
        human_deadline_at=iso_utc(task.human_deadline_at),
        created_at=iso_utc(task.created_at) or "",
        prompt_preview=_preview_text(prompt),
        api_key_name=task.api_key_name_snapshot,
        display_name=_task_display_name(task),
        has_tools=bool(tool_names),
        unread=seen is None,
        seen_at=iso_utc(seen.seen_at) if seen else None,
        last_seen_event_id=(
            str(seen.last_seen_event_id) if seen and seen.last_seen_event_id else None
        ),
        owner_user_id=str(task.owner_user_id) if include_owner else None,
        owner_username=owner_username,
    )


@router.post("/{task_id}/seen", status_code=204)
def mark_task_seen(
    task_id: int,
    body: SeenBody | None = None,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """标记收件箱任务已读（独立短事务，失败不阻塞前端）。"""
    task = _get_task(db, task_id, user)
    if task.owner_user_id != user.id:
        raise DomainError(DomainErrorCode.FORBIDDEN, "仅所有者能标记已读", status_code=403)
    _service.repo.upsert_seen(
        db,
        task_id=task.id,
        owner_user_id=user.id,
        last_seen_event_id=(body.last_seen_event_id if body else None),
    )
    db.commit()
    return Response(status_code=204)


# ----------------------------------------------------------------------
# 请求视图（RequestView）
# ----------------------------------------------------------------------


@router.get("/{task_id}/request-view", response_model=RequestViewResponse)
def get_request_view(
    task_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> RequestViewResponse:
    """分区后的本次请求视图（只投影本次请求，不做会话历史语义）。

    管理员可只读查看；确认警告、生成草稿、提交回复仍仅限所有者。
    """
    task = _get_task(db, task_id, user)
    view = _view_service.get_request_view(db, task)
    return _request_view_response(view)


@router.get("/{task_id}/request-view/blocks/{block_id}")
def get_request_view_block(
    task_id: int,
    block_id: str,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按需返回附件或超长内容块的完整内容（base64 仅在此端点出现）。"""
    task = _get_task(db, task_id, user)
    return _view_service.get_block(db, task, block_id)


def _request_view_response(view: dict[str, Any]) -> RequestViewResponse:
    task_view = view["task"]
    return RequestViewResponse(
        task=RequestViewTask(**task_view),
        current_input=[_context_item_view(item) for item in view["current_input"]],
        caller_system=CallerSystemView(
            items=[_context_item_view(item) for item in view["caller_system"]["items"]],
            item_count=view["caller_system"]["item_count"],
            character_count=view["caller_system"]["character_count"],
            collapsed_by_default=view["caller_system"]["collapsed_by_default"],
        ),
        attached_context=[_context_item_view(item) for item in view["attached_context"]],
        attachments=[_content_block_view(block) for block in view["attachments"]],
        caller_tools=CallerToolsView(
            definitions=[
                ToolDefinitionView(
                    name=tool["name"],
                    description=tool["description"],
                    input_schema=tool["input_schema"],
                    source_type=tool["source_type"],
                    is_generatable=tool["is_generatable"],
                )
                for tool in view["caller_tools"]["definitions"]
            ],
            choice=view["caller_tools"]["choice"],
            required_name=view["caller_tools"]["required_name"],
            parallel_allowed=view["caller_tools"]["parallel_allowed"],
        ),
        tool_call_warning=ToolCallWarningView(**view["tool_call_warning"]),
        raw_request_available=view["raw_request_available"],
    )


# ----------------------------------------------------------------------
# 一次性 Tool Call 风险告知（§7.2）
# ----------------------------------------------------------------------


@router.post("/{task_id}/tool-call-warning/acknowledge", response_model=WarningAcknowledgeView)
def acknowledge_tool_call_warning(
    task_id: int,
    body: AcknowledgeBody | None = None,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> WarningAcknowledgeView:
    """幂等确认本任务的 Caller Tool 风险告知（每 RequestTask 一次）。

    仅任务所有者可确认；任务没有 Caller Tool 时 400
    caller_tools_not_available；管理员不能代确认。
    """
    task = _get_task(db, task_id, user)
    if task.owner_user_id != user.id or user.role is UserRole.ADMIN:
        raise DomainError(
            DomainErrorCode.FORBIDDEN, "仅任务所有者能确认工具风险告知", status_code=403
        )
    if catalog_for_task(task).is_empty:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "当前请求没有声明 Caller Tool，无需确认",
            status_code=400,
            public_code="caller_tools_not_available",
        )
    from ..core.logging import log_event

    row = _service.repo.acknowledge_tool_call_warning(db, task_id=task.id, owner_user_id=user.id)
    from ..repositories.system import AuditRepository

    AuditRepository().add(
        db,
        action=AuditAction.TASK_TOOL_CALL_WARNING_ACKNOWLEDGED,
        resource_type="request_task",
        resource_id=str(task.id),
        actor_user_id=user.id,
        owner_user_id=task.owner_user_id,
    )
    log_event(
        "info",
        "task.tool_call_warning.acknowledged",
        "任务已确认 Caller Tool 风险告知",
        task_id=task.id,
        user_id=user.id,
    )
    db.commit()
    return WarningAcknowledgeView(
        task_id=str(task.id),
        acknowledged=True,
        acknowledged_at=iso_utc(row.tool_call_warning_acknowledged_at) or "",
    )


# ----------------------------------------------------------------------
# 指定工具参数生成（§7.4）
# ----------------------------------------------------------------------


@router.post(
    "/{task_id}/tools/{tool_name}/arguments/generate",
    response_model=ToolArgumentsGenerateView,
)
async def generate_tool_arguments(
    task_id: int,
    tool_name: str,
    payload: ToolArgumentsGenerateInput,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ToolArgumentsGenerateView:
    """使用指定 LLM 配置为指定 Caller Tool 生成参数建议。

    结果只是可编辑建议：不保存草稿、不创建 Tool Call、不提交任务、更不
    执行工具（网关零执行）。首次校验失败自动修复一次，仍失败返回 502
    generated_tool_arguments_invalid。
    """
    from ..services.tool_argument_generation_service import ToolArgumentGenerationService

    task = _get_task(db, task_id, user)
    generation_instruction = normalize_generation_instruction(payload.generation_instruction)
    service = ToolArgumentGenerationService()
    result = await service.generate(
        db,
        task=task,
        owner=user,
        tool_name=tool_name,
        llm_config_id=payload.llm_config_id,
        generation_instruction=generation_instruction,
        include_caller_system=payload.include_caller_system,
        excluded_context_item_ids=payload.excluded_context_item_ids,
        include_attachments=payload.include_attachments,
        current_arguments=payload.current_arguments,
    )
    db.commit()
    return ToolArgumentsGenerateView(
        tool_name=tool_name,
        arguments=result["arguments"],
        llm_config_id=str(result["llm_config_id"]),
        schema_valid=True,
        warnings=result.get("warnings", []),
    )

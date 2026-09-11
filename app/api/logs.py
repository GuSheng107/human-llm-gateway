"""日志与控制台统计 API。

统一日志查询（GET /api/logs）：合并审计日志与应用日志，按 trace_id 串联，
普通用户只能看到与自己相关的行。控制台统计（GET /api/dashboard）为全站
同一口径。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc, utc_now
from ..domain.enums import FakeModelScope, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.tasks import TERMINAL_STATES
from ..repositories.models import (
    ApiKey,
    AppLog,
    AuditLog,
    FakeModel,
    RequestTask,
    User,
)
from ..repositories.system import AppLogRepository, AuditRepository
from .deps import require_current_user

router = APIRouter(prefix="/api", tags=["logs"])

_audit_repo = AuditRepository()
_applog_repo = AppLogRepository()


# ----------------------------------------------------------------------
# 视图模型
# ----------------------------------------------------------------------


class LogEntry(BaseModel):
    """统一日志条目：合并视图下的一行（审计或应用日志）。

    - kind: 'audit' / 'app'
    - level: 应用日志等级或审计结果
    - event: 应用日志事件名 / 审计动作
    - message: 应用日志消息；审计日志为 "动作 资源(可选资源ID)" 摘要
    - username/user_id: 触发人（审计=actor；应用日志=自身归属）
    - request_id: 统一 traceId
    - task_id / api_key_id / connection_id: 仅应用日志有
    - has_detail / detail_size_bytes / detail_truncated: 详情信封元数据
      （列表不返回 detail_json，正文经 GET /api/logs/{entry_id} 懒加载）
    - duration_ms / status_code: 来自轻量 context 的固定键（仅应用日志）
    """

    id: str
    kind: Literal["audit", "app"]
    category: str
    level: str
    event: str
    message: str
    username: str | None
    user_id: str | None
    request_id: str | None
    task_id: str | None = None
    api_key_id: str | None = None
    connection_id: str | None = None
    context: dict[str, Any] | None = None
    has_detail: bool = False
    detail_size_bytes: int = 0
    detail_truncated: bool = False
    duration_ms: int | None = None
    status_code: int | None = None
    created_at: str


class LogPage(BaseModel):
    items: list[LogEntry]
    page: int
    page_size: int
    total: int


class LogDetailResponse(BaseModel):
    """单条日志详情（audit-123 / app-456；懒加载）。"""

    id: str
    kind: Literal["audit", "app"]
    category: str
    level: str
    event: str
    message: str
    username: str | None
    user_id: str | None
    request_id: str | None
    task_id: str | None = None
    api_key_id: str | None = None
    connection_id: str | None = None
    context: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None
    detail_size_bytes: int = 0
    detail_truncated: bool = False
    created_at: str


class DashboardStats(BaseModel):
    """全站统一口径：所有用户看到同一组数据，控制台不区分用户身份。"""

    total_users: int = 0
    active_users: int = 0
    total_tasks: int = 0
    active_tasks: int = 0
    total_api_keys: int = 0
    active_models: int = 0


class RecentTask(BaseModel):
    id: str
    public_id: str
    model: str
    protocol: str
    state: str
    created_at: str
    human_deadline_at: str | None = None


class DailyTaskPoint(BaseModel):
    date: str
    count: int


class ProtocolCount(BaseModel):
    protocol: str
    count: int


class DashboardResponse(BaseModel):
    stats: DashboardStats
    recent_tasks: list[RecentTask]
    daily_tasks: list[DailyTaskPoint]
    protocol_counts: list[ProtocolCount]


# ----------------------------------------------------------------------
# 统一日志查询（合并审计 + 应用日志）
# ----------------------------------------------------------------------


def _parse_context(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _parse_datetime(raw: str | None) -> Any | None:
    if not raw:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _load_detail(row: AppLog) -> dict[str, Any] | None:
    if not row.detail_json:
        return None
    try:
        value = json.loads(row.detail_json)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _to_log_entry_from_audit(row: AuditLog, actor_username: str | None) -> LogEntry:
    return LogEntry(
        id=f"audit-{row.id}",
        kind="audit",
        category="audit",
        level=row.result.value,
        event=row.action,
        message=f"{row.action} {row.resource_type}"
        + (f"#{row.resource_id}" if row.resource_id else ""),
        username=actor_username,
        user_id=str(row.actor_user_id) if row.actor_user_id is not None else None,
        request_id=row.request_id,
        context=_parse_context(row.metadata_json),
        created_at=iso_utc(row.created_at) or "",
    )


def _to_log_entry_from_app(row: AppLog, username: str | None) -> LogEntry:
    context = _parse_context(row.context_json)
    duration_ms = None
    status_code = None
    if context:
        raw_duration = context.get("duration_ms")
        if isinstance(raw_duration, (int, float)):
            duration_ms = int(raw_duration)
        raw_status = context.get("status_code") or context.get("http_status")
        if isinstance(raw_status, int):
            status_code = raw_status
    return LogEntry(
        id=f"app-{row.id}",
        kind="app",
        category=row.event.split(".", 1)[0] if row.event else "app",
        level=row.level,
        event=row.event,
        message=row.message,
        username=username,
        user_id=str(row.user_id) if row.user_id is not None else None,
        request_id=row.request_id,
        task_id=str(row.task_id) if row.task_id is not None else None,
        api_key_id=str(row.api_key_id) if row.api_key_id is not None else None,
        connection_id=str(row.connection_id) if row.connection_id is not None else None,
        context=context,
        has_detail=bool(row.detail_json),
        detail_size_bytes=row.detail_size_bytes or 0,
        detail_truncated=bool(row.detail_truncated),
        duration_ms=duration_ms,
        status_code=status_code,
        created_at=iso_utc(row.created_at) or "",
    )


@router.get("/logs", response_model=LogPage)
def list_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    kind: Literal["audit", "app"] | None = Query(default=None),
    trace_id: str | None = Query(default=None, max_length=64),
    event: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    level: Literal["debug", "error", "warning", "info"] | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=24 * 30),
    task_id: int | None = Query(default=None, ge=1),
    api_key_id: int | None = Query(default=None, ge=1),
    connection_id: int | None = Query(default=None, ge=1),
    start_at: str | None = Query(default=None, max_length=40),
    end_at: str | None = Query(default=None, max_length=40),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LogPage:
    """合并审计与应用日志；按 kind/trace/event/级别/关联 ID/时间过滤。

    - 管理员：可见全站；
    - 普通用户：仅 actor/owner 为自己（审计），或归属自己资源（应用日志）。
    - 列表不返回 detail_json；正文经 GET /api/logs/{entry_id} 懒加载。
    """
    is_admin = user.role is UserRole.ADMIN
    # 每侧拿足窗口条数再合并切片。page*page_size 上界 100*100 = 10k，SQLite 可接受。
    fetch = min(page * page_size, 10000)

    category_filter = category.strip() if category else None
    event_filter = event.strip() if event else None
    audit_enabled = kind != "app" and category_filter in (None, "audit")
    app_enabled = kind != "audit" and category_filter != "audit"

    start_at_dt = _parse_datetime(start_at)
    end_at_dt = _parse_datetime(end_at)

    # 1) 审计日志（level 仅定义应用日志等级；带 level 时不混入审计结果）。
    if level is not None or not audit_enabled:
        audit_rows, audit_total = [], 0
    elif is_admin:
        audit_rows, audit_total = _audit_repo.list_page(
            db,
            page=1,
            page_size=fetch,
            action=event_filter,
            request_id=trace_id,
            hours=hours,
            start_at=start_at_dt,
            end_at=end_at_dt,
        )
    else:
        audit_rows = _audit_repo.list_for_subject(
            db,
            subject_user_id=user.id,
            limit=fetch,
            hours=hours,
            request_id=trace_id,
            action=event_filter,
            start_at=start_at_dt,
            end_at=end_at_dt,
        )
        # 非管理员的全量计数不必精确（合并视图只需要条目本身）。
        audit_total = len(audit_rows)

    # 2) 应用日志（复用现有 scope 过滤）
    if app_enabled:
        app_rows, app_total = _applog_repo.list_page(
            db,
            page=1,
            page_size=fetch,
            event=event_filter,
            category=category_filter,
            level=level,
            request_id=trace_id,
            task_id=task_id,
            api_key_id=api_key_id,
            connection_id=connection_id,
            start_at=start_at_dt,
            end_at=end_at_dt,
            scope_owner_id=None if is_admin else user.id,
        )
    else:
        app_rows, app_total = [], 0

    # 3) 用户名映射（审计 actor 与应用日志 user_id 统一）
    from sqlalchemy import select as _select

    from ..repositories.models import User as _User

    user_ids: set[int] = set()
    for row in audit_rows:
        if row.actor_user_id is not None:
            user_ids.add(row.actor_user_id)
    for row in app_rows:
        if row.user_id is not None:
            user_ids.add(row.user_id)
    username_map: dict[int, str] = {}
    if user_ids:
        username_map = {
            row_user.id: row_user.username
            for row_user in db.scalars(_select(_User).where(_User.id.in_(user_ids)))
        }

    entries: list[LogEntry] = []
    for row in audit_rows:
        entries.append(
            _to_log_entry_from_audit(
                row,
                actor_username=(username_map.get(row.actor_user_id) if row.actor_user_id else None),
            )
        )
    for row in app_rows:
        username = username_map.get(row.user_id) if row.user_id is not None else None
        entries.append(_to_log_entry_from_app(row, username))

    entries.sort(key=lambda item: item.created_at, reverse=True)

    start = (page - 1) * page_size
    end = start + page_size
    total = audit_total + app_total
    return LogPage(
        items=entries[start:end],
        page=page,
        page_size=page_size,
        total=total,
    )


# ----------------------------------------------------------------------
# 日志详情（懒加载，§7.5）
# ----------------------------------------------------------------------


@router.get("/logs/{entry_id}", response_model=LogDetailResponse)
def get_log_detail(
    entry_id: str,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LogDetailResponse:
    """按需加载单条日志详情（entry_id 延续 audit-123 / app-456 格式）。

    权限：管理员可见全站；普通用户仅 actor/owner 为自己（审计）或归属
    自己资源（应用日志）。不存在与无权访问统一返回 404，避免枚举。
    """
    is_admin = user.role is UserRole.ADMIN
    kind, _, raw_id = entry_id.partition("-")
    if kind not in {"audit", "app"} or not raw_id.isdigit():
        raise _not_found()
    numeric_id = int(raw_id)

    if kind == "audit":
        row = db.get(AuditLog, numeric_id)
        if row is None or not _audit_detail_visible(row, user, is_admin):
            raise _not_found()
        username = _username(db, row.actor_user_id)
        return LogDetailResponse(
            id=f"audit-{row.id}",
            kind="audit",
            category="audit",
            level=row.result.value,
            event=row.action,
            message=f"{row.action} {row.resource_type}"
            + (f"#{row.resource_id}" if row.resource_id else ""),
            username=username,
            user_id=str(row.actor_user_id) if row.actor_user_id is not None else None,
            request_id=row.request_id,
            context=_parse_context(row.metadata_json),
            detail=None,
            created_at=iso_utc(row.created_at) or "",
        )

    row = _applog_repo.get_entry(db, numeric_id)
    if row is None or not _app_detail_visible(row, user, is_admin, db):
        raise _not_found()
    username = _username(db, row.user_id)
    return LogDetailResponse(
        id=f"app-{row.id}",
        kind="app",
        category=row.event.split(".", 1)[0] if row.event else "app",
        level=row.level,
        event=row.event,
        message=row.message,
        username=username,
        user_id=str(row.user_id) if row.user_id is not None else None,
        request_id=row.request_id,
        task_id=str(row.task_id) if row.task_id is not None else None,
        api_key_id=str(row.api_key_id) if row.api_key_id is not None else None,
        connection_id=str(row.connection_id) if row.connection_id is not None else None,
        context=_parse_context(row.context_json),
        detail=_load_detail(row),
        detail_size_bytes=row.detail_size_bytes or 0,
        detail_truncated=bool(row.detail_truncated),
        created_at=iso_utc(row.created_at) or "",
    )


def _not_found() -> DomainError:
    return DomainError(DomainErrorCode.NOT_FOUND, "日志不存在", status_code=404)


def _username(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = db.get(User, user_id)
    return row.username if row else None


def _audit_detail_visible(row: AuditLog, user: User, is_admin: bool) -> bool:
    if is_admin:
        return True
    return row.actor_user_id == user.id or row.owner_user_id == user.id


def _app_detail_visible(row: AppLog, user: User, is_admin: bool, db: Session) -> bool:
    """详情权限必须重新执行范围过滤，不能先按 ID 取出再判断（§7.5）。"""
    if is_admin:
        return True
    if row.user_id == user.id:
        return True
    # 归属自己资源：自己的 Key / 连接 / 任务。
    from ..repositories.models import ApiKey, ImConnection, RequestTask

    if row.api_key_id is not None:
        key = db.get(ApiKey, row.api_key_id)
        if key and key.owner_user_id == user.id:
            return True
    if row.connection_id is not None:
        conn = db.get(ImConnection, row.connection_id)
        if conn and conn.owner_user_id == user.id:
            return True
    if row.task_id is not None:
        task = db.get(RequestTask, row.task_id)
        if task and task.owner_user_id == user.id:
            return True
    return False


# ----------------------------------------------------------------------
# 控制台统计
# ----------------------------------------------------------------------


def _count(session: Session, model: Any, *conditions: Any) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> DashboardResponse:
    """控制台统计：全站统一口径，所有用户看到同一组数据。"""
    stats = DashboardStats(
        total_users=_count(db, User),
        active_users=_count(db, User, User.is_active.is_(True)),
        total_tasks=_count(db, RequestTask),
        active_tasks=_count(db, RequestTask, RequestTask.state.not_in(list(TERMINAL_STATES))),
        total_api_keys=_count(db, ApiKey),
        # “可用模型” = 已启用的管理员系统模型 + 当前用户已启用的私有模型。
        # 私有模型严格按当前用户过滤，不能把其他用户的“+数量”算进来。
        active_models=_count(
            db,
            FakeModel,
            FakeModel.is_enabled.is_(True),
            (FakeModel.scope == FakeModelScope.SYSTEM) | (FakeModel.owner_user_id == user.id),
        ),
    )
    # 任务列表保留用户最近任务（控制台其它数据为全站口径）。
    recent_rows = list(
        db.scalars(
            select(RequestTask)
            .where(RequestTask.owner_user_id == user.id)
            .order_by(RequestTask.id.desc())
            .limit(8)
        )
    )
    start_day = (utc_now() - timedelta(days=6)).date()
    daily_counts = {
        str(day): int(count)
        for day, count in db.execute(
            select(func.date(RequestTask.created_at), func.count())
            .where(RequestTask.created_at >= utc_now() - timedelta(days=7))
            .group_by(func.date(RequestTask.created_at))
        )
    }
    protocol_counts = [
        ProtocolCount(protocol=protocol.value, count=int(count))
        for protocol, count in db.execute(
            select(RequestTask.protocol, func.count()).group_by(RequestTask.protocol)
        )
    ]

    def recent_task(row: RequestTask) -> RecentTask:
        return RecentTask(
            id=str(row.id),
            public_id=row.public_id,
            model=row.requested_model,
            protocol=row.protocol.value,
            state=row.state.value,
            created_at=iso_utc(row.created_at) or "",
            human_deadline_at=iso_utc(row.human_deadline_at),
        )

    return DashboardResponse(
        stats=stats,
        recent_tasks=[recent_task(row) for row in recent_rows],
        daily_tasks=[
            DailyTaskPoint(
                date=(start_day + timedelta(days=offset)).isoformat(),
                count=daily_counts.get((start_day + timedelta(days=offset)).isoformat(), 0),
            )
            for offset in range(7)
        ],
        protocol_counts=protocol_counts,
    )

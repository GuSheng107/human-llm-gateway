"""日志与控制台统计 API（docs/API_CONTRACT.md §11，M9）。

仅管理员；审计与应用日志都支持筛选分页，响应不包含请求正文或任何
凭据恢复材料（metadata 只含字段名，AppLog context 已过 sanitize）。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc, utc_now
from ..domain.enums import TaskState, UserRole
from ..domain.tasks import TERMINAL_STATES
from ..repositories.models import (
    ApiKey,
    AuditLog,
    ImConnection,
    LlmConfig,
    RequestTask,
    User,
)
from ..repositories.system import AppLogRepository, AuditRepository
from .deps import require_admin, require_current_user

router = APIRouter(prefix="/api", tags=["logs"])

_audit_repo = AuditRepository()
_applog_repo = AppLogRepository()


# ----------------------------------------------------------------------
# 视图模型
# ----------------------------------------------------------------------


class AuditLogView(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None
    actor_user_id: str | None
    actor_username: str | None
    owner_user_id: str | None
    result: str
    request_id: str | None
    fields: list[str]
    created_at: str


class AuditLogPage(BaseModel):
    items: list[AuditLogView]
    page: int
    page_size: int
    total: int


class AppLogView(BaseModel):
    id: str
    level: str
    event: str
    message: str
    request_id: str | None
    user_id: str | None
    task_id: str | None
    api_key_id: str | None
    connection_id: str | None
    created_at: str


class AppLogPage(BaseModel):
    items: list[AppLogView]
    page: int
    page_size: int
    total: int


class DashboardStats(BaseModel):
    role: str
    # 用户视角
    my_active_tasks: int = 0
    my_total_tasks: int = 0
    my_api_keys: int = 0
    my_llm_configs: int = 0
    # 管理员视角
    total_users: int = 0
    active_users: int = 0
    total_tasks: int = 0
    global_active_tasks: int = 0
    total_api_keys: int = 0
    total_connections: int = 0


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


class ConnectionHealth(BaseModel):
    id: str
    name: str
    platform: str
    state: str
    retry_count: int
    last_error: str | None


class DashboardResponse(BaseModel):
    stats: DashboardStats
    recent_tasks: list[RecentTask]
    daily_tasks: list[DailyTaskPoint]
    protocol_counts: list[ProtocolCount]
    urgent_tasks: list[RecentTask]
    problem_tasks: list[RecentTask]
    connection_health: list[ConnectionHealth]


# ----------------------------------------------------------------------
# 审计日志
# ----------------------------------------------------------------------


def _audit_view(session: Session, row: AuditLog) -> AuditLogView:
    actor_username = None
    if row.actor_user_id is not None:
        actor = session.get(User, row.actor_user_id)
        actor_username = actor.username if actor else None
    fields: list[str] = []
    if row.metadata_json:
        try:
            parsed = json.loads(row.metadata_json)
            if isinstance(parsed, dict):
                raw = parsed.get("fields")
                if isinstance(raw, list):
                    fields = [str(item) for item in raw]
        except (ValueError, TypeError):
            fields = []
    return AuditLogView(
        id=str(row.id),
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
        actor_username=actor_username,
        owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
        result=row.result.value,
        request_id=row.request_id,
        fields=fields,
        created_at=iso_utc(row.created_at) or "",
    )


@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor_user_id: int | None = Query(default=None),
    resource_type: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=100),
    owner_user_id: int | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=24 * 30),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditLogPage:
    rows, total = _audit_repo.list_page(
        db,
        page=page,
        page_size=page_size,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        action=action,
        owner_user_id=owner_user_id,
        hours=hours,
    )
    return AuditLogPage(
        items=[_audit_view(db, row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


# ----------------------------------------------------------------------
# 应用日志
# ----------------------------------------------------------------------


@router.get("/app-logs", response_model=AppLogPage)
def list_app_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    level: str | None = Query(default=None, pattern="^(debug|info|warning|error)$"),
    event: str | None = Query(default=None, max_length=100),
    user_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    connection_id: int | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=24 * 30),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AppLogPage:
    rows, total = _applog_repo.list_page(
        db,
        page=page,
        page_size=page_size,
        level=level,
        event=event,
        user_id=user_id,
        task_id=task_id,
        api_key_id=api_key_id,
        connection_id=connection_id,
        hours=hours,
    )
    return AppLogPage(
        items=[
            AppLogView(
                id=str(row.id),
                level=row.level,
                event=row.event,
                message=row.message,
                request_id=row.request_id,
                user_id=str(row.user_id) if row.user_id else None,
                task_id=str(row.task_id) if row.task_id else None,
                api_key_id=str(row.api_key_id) if row.api_key_id else None,
                connection_id=str(row.connection_id) if row.connection_id else None,
                created_at=iso_utc(row.created_at) or "",
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


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
    """控制台统计：普通用户看个人概览，管理员追加全局治理数据。"""
    is_admin = user.role is UserRole.ADMIN
    stats = DashboardStats(role=user.role.value)
    if not is_admin:
        stats.my_active_tasks = _count(
            db,
            RequestTask,
            RequestTask.owner_user_id == user.id,
            RequestTask.state.not_in(list(TERMINAL_STATES)),
        )
        stats.my_total_tasks = _count(db, RequestTask, RequestTask.owner_user_id == user.id)
        stats.my_api_keys = _count(db, ApiKey, ApiKey.owner_user_id == user.id)
        stats.my_llm_configs = _count(db, LlmConfig, LlmConfig.owner_user_id == user.id)
    else:
        stats.total_users = _count(db, User)
        stats.active_users = _count(db, User, User.is_active.is_(True))
        stats.total_tasks = _count(db, RequestTask)
        stats.global_active_tasks = _count(
            db, RequestTask, RequestTask.state.not_in(list(TERMINAL_STATES))
        )
        stats.total_api_keys = _count(db, ApiKey)
        stats.total_connections = _count(db, ImConnection)

    scope = [] if is_admin else [RequestTask.owner_user_id == user.id]
    recent_rows = list(
        db.scalars(select(RequestTask).where(*scope).order_by(RequestTask.id.desc()).limit(8))
    )
    start_day = (utc_now() - timedelta(days=6)).date()
    daily_counts = {
        str(day): int(count)
        for day, count in db.execute(
            select(func.date(RequestTask.created_at), func.count())
            .where(*scope, RequestTask.created_at >= utc_now() - timedelta(days=7))
            .group_by(func.date(RequestTask.created_at))
        )
    }
    protocol_counts = [
        ProtocolCount(protocol=protocol.value, count=int(count))
        for protocol, count in db.execute(
            select(RequestTask.protocol, func.count()).where(*scope).group_by(RequestTask.protocol)
        )
    ]
    urgent_rows = list(
        db.scalars(
            select(RequestTask)
            .where(
                *scope,
                RequestTask.state.not_in(list(TERMINAL_STATES)),
                RequestTask.human_deadline_at.is_not(None),
            )
            .order_by(RequestTask.human_deadline_at.asc())
            .limit(5)
        )
    )
    problem_rows = list(
        db.scalars(
            select(RequestTask)
            .where(
                *scope,
                RequestTask.state.in_([TaskState.FAILED, TaskState.TIMED_OUT, TaskState.CANCELLED]),
            )
            .order_by(RequestTask.id.desc())
            .limit(5)
        )
    )

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

    connections: list[ConnectionHealth] = []
    if is_admin:
        connection_rows = list(
            db.scalars(
                select(ImConnection)
                .order_by(ImConnection.state.asc(), ImConnection.id.desc())
                .limit(8)
            )
        )
        connections = [
            ConnectionHealth(
                id=str(row.id),
                name=row.name,
                platform=row.platform,
                state=row.state.value,
                retry_count=row.retry_count,
                last_error=row.last_error_message or row.last_error_code,
            )
            for row in connection_rows
        ]

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
        urgent_tasks=[recent_task(row) for row in urgent_rows],
        problem_tasks=[recent_task(row) for row in problem_rows],
        connection_health=connections,
    )

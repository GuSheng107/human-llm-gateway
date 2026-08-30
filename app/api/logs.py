"""日志与控制台统计 API（docs/API_CONTRACT.md §11，M9）。

仅管理员；审计与应用日志都支持筛选分页，响应不包含请求正文或任何
凭据恢复材料（metadata 只含字段名，AppLog context 已过 sanitize）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import UserRole
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


class DashboardResponse(BaseModel):
    stats: DashboardStats
    recent_tasks: list[RecentTask]


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

    recent_rows = list(
        db.scalars(
            select(RequestTask)
            .where(RequestTask.owner_user_id == user.id if not is_admin else True)
            .order_by(RequestTask.id.desc())
            .limit(8)
        )
    )
    return DashboardResponse(
        stats=stats,
        recent_tasks=[
            RecentTask(
                id=str(row.id),
                public_id=row.public_id,
                model=row.requested_model,
                protocol=row.protocol.value,
                state=row.state.value,
                created_at=iso_utc(row.created_at) or "",
            )
            for row in recent_rows
        ],
    )

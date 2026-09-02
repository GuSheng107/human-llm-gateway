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
from ..domain.enums import FakeModelScope, UserRole
from ..domain.tasks import TERMINAL_STATES
from ..repositories.models import (
    ApiKey,
    AuditLog,
    FakeModel,
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
    logger: str | None
    user_id: str | None
    username: str | None
    task_id: str | None
    api_key_id: str | None
    connection_id: str | None
    created_at: str


class AppLogDetail(AppLogView):
    """单条日志详情：携带脱敏后的 context 与异常信息。"""

    context: dict[str, Any] | None = None


class AppLogPage(BaseModel):
    items: list[AppLogView | AppLogDetail]
    page: int
    page_size: int
    total: int


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
    request_id: str | None = Query(default=None, max_length=64),
    hours: int | None = Query(default=None, ge=1, le=24 * 30),
    with_context: bool = Query(default=False),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> AppLogPage:
    # 非管理员只能看到自己资源范围内的日志（scope 过滤在仓库层完成，
    # 覆盖 user/Key/连接/任务多条归属链）。
    scope_owner_id = None if user.role is UserRole.ADMIN else user.id
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
        request_id=request_id,
        hours=hours,
        scope_owner_id=scope_owner_id,
    )

    def _context_of(row) -> dict[str, Any] | None:
        if not row.context_json:
            return None
        try:
            parsed = json.loads(row.context_json)
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None

    # 本页内统一解析用户名（user_id -> username），避免逐行查询。
    from ..repositories.models import User as _User

    user_ids = sorted({row.user_id for row in rows if row.user_id is not None})
    username_map: dict[int, str] = {}
    if user_ids:
        username_map = {
            row_user.id: row_user.username
            for row_user in db.scalars(select(_User).where(_User.id.in_(user_ids)))
        }

    def _username(row) -> str | None:
        return username_map.get(row.user_id) if row.user_id is not None else None

    def _base(row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "level": row.level,
            "event": row.event,
            "message": row.message,
            "request_id": row.request_id,
            "logger": row.logger,
            "user_id": str(row.user_id) if row.user_id else None,
            "username": _username(row),
            "task_id": str(row.task_id) if row.task_id else None,
            "api_key_id": str(row.api_key_id) if row.api_key_id else None,
            "connection_id": str(row.connection_id) if row.connection_id else None,
            "created_at": iso_utc(row.created_at) or "",
        }

    if with_context:
        return AppLogPage(
            items=[AppLogDetail(**_base(row), context=_context_of(row)) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )
    return AppLogPage(
        items=[AppLogView(**_base(row)) for row in rows],
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

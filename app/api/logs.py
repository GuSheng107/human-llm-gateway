from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdminUser, AppLog, AuditLog
from .deps import paginate, pagination_params, require_admin
from .errors import ApiError, ErrorCode

router = APIRouter(prefix="/api", tags=["logs"])


def _parse_time(value: str | None):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "时间格式无效，请使用 ISO 格式")


@router.get("/audit-logs")
def list_audit_logs(
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    subject_id: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if subject_id:
        stmt = stmt.where(AuditLog.subject_id == subject_id)
    start_dt = _parse_time(start)
    if start_dt:
        stmt = stmt.where(AuditLog.created_at >= start_dt)
    end_dt = _parse_time(end)
    if end_dt:
        stmt = stmt.where(AuditLog.created_at <= end_dt)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    all_logs = list(db.execute(stmt).scalars())
    total = len(all_logs)
    page_start = (params["page"] - 1) * params["page_size"]
    items = [
        {
            "id": a.id,
            "action": a.action,
            "subject_type": a.subject_type,
            "subject_id": a.subject_id,
            "actor": a.actor,
            "detail": a.detail_json,
            "created_at": a.created_at.isoformat(),
        }
        for a in all_logs[page_start : page_start + params["page_size"]]
    ]
    return paginate(items, total, params)


@router.get("/app-logs")
def list_app_logs(
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
    level: str | None = Query(default=None),
    logger: str | None = Query(default=None),
    search: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    stmt = select(AppLog)
    conditions = []
    if level:
        conditions.append(AppLog.level == level)
    if logger:
        conditions.append(AppLog.logger == logger)
    if search:
        conditions.append(AppLog.message.contains(search))
    start_dt = _parse_time(start)
    if start_dt:
        conditions.append(AppLog.created_at >= start_dt)
    end_dt = _parse_time(end)
    if end_dt:
        conditions.append(AppLog.created_at <= end_dt)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(AppLog.created_at.desc())
    all_logs = list(db.execute(stmt).scalars())
    total = len(all_logs)
    page_start = (params["page"] - 1) * params["page_size"]
    items = [
        {
            "id": a.id,
            "level": a.level,
            "logger": a.logger,
            "message": a.message,
            "detail": a.detail_json,
            "created_at": a.created_at.isoformat(),
        }
        for a in all_logs[page_start : page_start + params["page_size"]]
    ]
    return paginate(items, total, params)

"""阶段 A：AuditRepository / AppLogRepository 自动继承当前 traceId。

行为契约：
- 当调用方在 ``bind_trace_id`` 上下文中调用 ``AuditRepository.add`` /
  ``AppLogRepository.add`` 且未显式传入 ``request_id`` 时，落库的
  ``request_id`` 必须等于 contextvar 中的 trace。
- 显式传入 ``request_id`` 时以调用方为准。
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import bind_trace_id, reset_request_id
from app.domain.enums import AuditAction, AuditResult
from app.repositories.models import AppLog, AuditLog
from app.repositories.system import AppLogRepository, AuditRepository


def test_audit_repository_inherits_trace_id_from_contextvar(client) -> None:
    token = bind_trace_id("trace-audit-default")
    try:
        with SessionLocal() as session:
            AuditRepository().add(
                session,
                action=AuditAction.USER_CREATED,
                resource_type="user",
                result=AuditResult.SUCCESS,
                actor_user_id=1,
                resource_id="42",
            )
            session.commit()
        with SessionLocal() as session:
            row = session.execute(
                select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
            ).scalar_one()
            assert row.request_id == "trace-audit-default"
    finally:
        reset_request_id(token)


def test_audit_repository_explicit_request_id_wins(client) -> None:
    token = bind_trace_id("trace-audit-context")
    try:
        with SessionLocal() as session:
            AuditRepository().add(
                session,
                action=AuditAction.USER_CREATED,
                resource_type="user",
                request_id="trace-audit-explicit",
            )
            session.commit()
        with SessionLocal() as session:
            row = session.execute(
                select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
            ).scalar_one()
            assert row.request_id == "trace-audit-explicit"
    finally:
        reset_request_id(token)


def test_app_log_repository_inherits_trace_id_from_contextvar(client) -> None:
    token = bind_trace_id("trace-applog-default")
    try:
        with SessionLocal() as session:
            AppLogRepository().add(
                session,
                level="info",
                event="unit.test",
                message="inherited trace",
            )
            session.commit()
        with SessionLocal() as session:
            row = session.execute(select(AppLog).order_by(AppLog.id.desc()).limit(1)).scalar_one()
            assert row.request_id == "trace-applog-default"
    finally:
        reset_request_id(token)

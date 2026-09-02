"""阶段 B：后台任务 / 独立短事务的 traceId 贯通。

行为契约：
- 看门狗 ``check_once`` 应当为每次调用生成独立 traceId（即便上层未绑定），
  该 trace 必须贯穿当轮所有 ``audit_logs.request_id``。
- 调用方若已绑定 traceId，看门狗应继承而非覆盖。
- 连接启动失败后的 ``_record_audit_after_commit`` 写审计时应当沿用当前
  请求 / 任务的 traceId；无上下文时也应分配一个内部 trace。
"""

from __future__ import annotations

import asyncio
import secrets

from sqlalchemy import select

import app.core.db as database
from app.core.logging import bind_trace_id, reset_request_id
from app.domain.enums import AuditAction, ConnectionState
from app.repositories.connections import ConnectionRepository
from app.repositories.models import AuditLog, ImConnection, User


def _seed_user_and_connection(
    *, state: ConnectionState, last_error_code: str | None = None
) -> tuple[int, int]:
    """插入测试用的连接并返回 (conn_id, owner_user_id)。"""
    with database.SessionLocal() as session:
        user = User(
            username=f"u-{secrets.token_hex(4)}",
            display_name="watchdog-trace",
            password_hash="x",
        )
        session.add(user)
        session.flush()
        row = ImConnection(
            owner_user_id=user.id,
            platform="wecom_aibot",
            name="trace-row",
            state=state,
            desired_running=True,
            config_ciphertext="",
            config_key_version=1,
            last_error_code=last_error_code,
        )
        session.add(row)
        session.commit()
        return row.id, user.id


def _audit_request_ids_for(conn_id: int) -> list[str]:
    with database.SessionLocal() as session:
        rows = (
            session.execute(
                select(AuditLog)
                .where(
                    AuditLog.resource_type == "im_connection",
                    AuditLog.resource_id == str(conn_id),
                )
                .order_by(AuditLog.id.desc())
                .limit(10)
            )
            .scalars()
            .all()
        )
    return [r.request_id for r in rows if r.request_id]


def test_watchdog_check_once_emits_unique_trace_per_call(client) -> None:
    """两次连续 check_once 应当为新种子连接使用不同的 traceId。"""
    from app.services.connection_watchdog import connection_watchdog

    conn_id_1, _ = _seed_user_and_connection(state=ConnectionState.ERROR, last_error_code="x")
    asyncio.run(connection_watchdog.check_once())
    ids_1 = _audit_request_ids_for(conn_id_1)
    assert ids_1, f"看门狗首轮应写出针对连接 {conn_id_1} 的审计"

    conn_id_2, _ = _seed_user_and_connection(state=ConnectionState.ERROR, last_error_code="x")
    asyncio.run(connection_watchdog.check_once())
    ids_2 = _audit_request_ids_for(conn_id_2)
    assert ids_2, f"看门狗次轮应写出针对连接 {conn_id_2} 的审计"
    assert ids_1[0] != ids_2[0], (
        f"两次 watch dog 周期应为新种子连接使用不同 traceId, ids_1={ids_1}, ids_2={ids_2}"
    )


def test_watchdog_check_once_inherits_caller_trace_id(client) -> None:
    """调用方绑定的 traceId 应当贯穿一次 check_once 的所有审计写入。"""
    from app.services.connection_watchdog import connection_watchdog

    conn_id, _ = _seed_user_and_connection(state=ConnectionState.ERROR, last_error_code="x")

    token = bind_trace_id("trace-watchdog-caller")
    try:
        asyncio.run(connection_watchdog.check_once())
    finally:
        reset_request_id(token)

    ids = _audit_request_ids_for(conn_id)
    assert "trace-watchdog-caller" in ids, f"调用方 traceId 应被继承, 实际: {ids}"


def test_record_audit_after_commit_inherits_trace_id(client) -> None:
    """ConnectionService._record_audit_after_commit 必须把当前 traceId 写入审计。"""
    from app.services.connection_service import ConnectionService

    conn_id, _ = _seed_user_and_connection(state=ConnectionState.STARTING)
    service = ConnectionService()

    with database.SessionLocal() as session:
        row = ConnectionRepository().get(session, conn_id)
        assert row is not None

    token = bind_trace_id("trace-record-audit")
    try:
        asyncio.run(
            service._record_audit_after_commit(
                row=row,
                action=AuditAction.CONNECTION_STARTED,
                actor_user_id=row.owner_user_id,
            )
        )
    finally:
        reset_request_id(token)

    with database.SessionLocal() as session:
        rows = (
            session.execute(
                select(AuditLog)
                .where(
                    AuditLog.resource_type == "im_connection",
                    AuditLog.resource_id == str(conn_id),
                    AuditLog.action == AuditAction.CONNECTION_STARTED.value,
                )
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
            .scalars()
            .all()
        )
    assert rows, "未找到审计记录"
    assert rows[0].request_id == "trace-record-audit"

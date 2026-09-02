"""IM 连接看门狗：周期检查运行状态，并关闭异常连接的启用开关。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextvars import Token
from typing import Any

from ..connectors import connection_manager as manager
from ..core.config import get_settings
from ..core.logging import bind_trace_id, get_request_id, log_event, new_trace_id, reset_request_id
from ..core.time import iso_utc, utc_now
from ..domain.enums import AuditAction, ConnectionState
from ..repositories.models import User
from ..repositories.system import AuditRepository
from .connection_service import ConnectionService

logger = logging.getLogger(__name__)

_ABNORMAL_STATES = {ConnectionState.AUTH_REQUIRED, ConnectionState.ERROR, ConnectionState.STOPPED}


class ConnectionWatchdog:
    def __init__(self) -> None:
        self.service = ConnectionService()
        self.audit = AuditRepository()
        self._lock = asyncio.Lock()

    async def check_once(self, *, owner_user_id: int | None = None) -> list[dict[str, Any]]:
        """检查可见连接；已启用且异常的连接会被自动停用。"""
        bound_token: Token[str | None] | None = None
        if get_request_id() is None:
            bound_token = bind_trace_id(new_trace_id())
        try:
            return await self._check_once_locked(owner_user_id=owner_user_id)
        finally:
            if bound_token is not None:
                reset_request_id(bound_token)

    async def _check_once_locked(self, *, owner_user_id: int | None) -> list[dict[str, Any]]:
        async with self._lock:
            from ..core.db import SessionLocal

            with SessionLocal() as session:
                rows = []
                page = 1
                while True:
                    batch, total = self.service.repo.list_page(
                        session,
                        page=page,
                        page_size=100,
                        owner_user_id=owner_user_id,
                    )
                    rows.extend(batch)
                    if len(rows) >= total or not batch:
                        break
                    page += 1
                reports: list[dict[str, Any]] = []
                for row in rows:
                    connector = manager.get_instance(row.id)
                    binding_expired = bool(
                        not row.desired_running
                        and row.bound_external_user_id is None
                        and row.binding_code_hash
                        and row.binding_code_expires_at
                        and row.binding_code_expires_at <= utc_now()
                    )
                    if connector is not None and binding_expired:
                        await manager.stop(row.id)
                        row.state = ConnectionState.STOPPED
                        row.next_retry_at = None
                        connector = None
                    runtime: dict[str, Any] = {"running": connector is not None}
                    check_error: str | None = None
                    if connector is not None:
                        try:
                            health = connector.health()
                            if inspect.isawaitable(health):
                                health = await health
                            runtime.update(health or {})
                        except Exception:
                            logger.exception(
                                "connection watchdog health check failed",
                                extra={"connection_id": row.id},
                            )
                            check_error = "连接器健康检查失败"
                            log_event(
                                "error",
                                "connection.watchdog_health_failed",
                                "连接器健康检查失败",
                                connection_id=row.id,
                                platform=row.platform,
                            )
                            runtime["running"] = False

                    # 任务活跃度：supervise 任务存在且未结束视为"启动中正常"，
                    # 避免 starting 与 desired_running=true 被判为异常停用。
                    supervisor_alive = manager.is_supervisor_alive(row.id)
                    runtime["supervisor_alive"] = supervisor_alive
                    abnormal = bool(
                        row.desired_running
                        and (
                            (row.state in {ConnectionState.AUTH_REQUIRED, ConnectionState.ERROR})
                            or check_error
                            or (row.state is ConnectionState.ONLINE and not runtime["running"])
                            or (row.state is ConnectionState.STOPPED and not supervisor_alive)
                            or (row.state is ConnectionState.STARTING and not supervisor_alive)
                        )
                    )
                    disabled = False
                    if abnormal:
                        await manager.stop(row.id)
                        row.desired_running = False
                        if check_error:
                            row.state = ConnectionState.ERROR
                            row.last_error_code = "health_check_failed"
                            row.last_error_message = check_error
                        disabled = True
                        log_event(
                            "warning",
                            "connection.watchdog_disabled",
                            "看门狗发现异常连接并关闭启用开关",
                            connection_id=row.id,
                            platform=row.platform,
                            state=row.state.value,
                            error_code=row.last_error_code,
                        )
                        self.audit.add(
                            session,
                            action=AuditAction.CONNECTION_WATCHDOG_DISABLED,
                            resource_type="im_connection",
                            resource_id=str(row.id),
                            owner_user_id=row.owner_user_id,
                            metadata={"reason": row.last_error_code or row.state.value},
                        )
                    row.last_health_at = utc_now()
                    owner = session.get(User, row.owner_user_id)
                    reports.append(
                        {
                            "id": str(row.id),
                            "name": row.name,
                            "platform": row.platform,
                            "platform_label": (
                                self.service.registry.get_spec(row.platform).label
                                if self.service.registry.get_spec(row.platform)
                                else row.platform
                            ),
                            "owner_username": owner.username if owner else None,
                            "state": row.state.value,
                            "desired_running": row.desired_running,
                            "bound": row.bound_external_user_id is not None,
                            "retry_count": row.retry_count,
                            "next_retry_at": iso_utc(row.next_retry_at),
                            "last_authenticated_at": iso_utc(row.last_authenticated_at),
                            "last_health_at": iso_utc(row.last_health_at),
                            "last_error_code": row.last_error_code,
                            "last_error_message": row.last_error_message,
                            "runtime": runtime,
                            "abnormal": abnormal,
                            "auto_disabled": disabled,
                        }
                    )
                session.commit()
                return reports

    async def run(self) -> None:
        """应用级周期循环；每轮独立失败，不终止后续检查。"""
        interval = get_settings().connection_watchdog_interval_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("connection watchdog cycle failed")
                log_event("error", "connection.watchdog_cycle_failed", "看门狗检查周期失败")


connection_watchdog = ConnectionWatchdog()

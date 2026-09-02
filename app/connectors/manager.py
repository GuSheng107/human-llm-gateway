"""连接管理器：连接器实例生命周期与故障恢复协调。

单连接启动、停止、重新应用配置或故障恢复只影响目标连接；
期望运行的长连接按带抖动指数退避自动重连，auth_required 停止重试。
运行状态通过注入的 state_recorder 持久化，管理器不直接依赖数据库。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.constants import CONNECTION_HEALTHY_RESET_SECONDS
from ..core.time import utc_now
from ..domain.connections import ConnectorError, backoff_delay
from ..domain.enums import ConnectionState
from .base import Connector, ConnectorContext, InboundMessage
from .registry import ConnectorRegistry, default_registry

logger = logging.getLogger(__name__)

# state_recorder(connection_id, patch)：把运行时状态补丁写入数据库。
StateRecorder = Callable[[int, dict[str, Any]], Awaitable[None]]
# inbound_handler(connection_id, message)：统一进站回调（服务层提供）。
InboundHandler = Callable[[int, InboundMessage], Awaitable[Any]]
RowLike = Any  # ImConnection 行（避免管理器依赖 ORM 模型）


class ConnectionManager:
    def __init__(self, registry: ConnectorRegistry = default_registry) -> None:
        self._registry = registry
        self._instances: dict[int, Connector] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._stopping: set[int] = set()
        self._state_recorder: StateRecorder | None = None

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    def set_state_recorder(self, recorder: StateRecorder) -> None:
        self._state_recorder = recorder

    @property
    def running_ids(self) -> list[int]:
        return sorted(self._instances)

    def get_instance(self, connection_id: int) -> Connector | None:
        return self._instances.get(connection_id)

    def is_running(self, connection_id: int) -> bool:
        connector = self._instances.get(connection_id)
        return connector is not None

    def is_supervisor_alive(self, connection_id: int) -> bool:
        """监督任务是否仍在运行（未结束）。

        用于看门狗判定 starting 中连接是否真在启动：
        supervisor 存在且未 done 才算"正在拉起"，避免 desired_running=true
        + state=starting 被当成异常停用。
        """
        task = self._tasks.get(connection_id)
        return task is not None and not task.done()

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------

    async def _record(self, connection_id: int, patch: dict[str, Any]) -> None:
        if self._state_recorder is None:
            return
        try:
            await self._state_recorder(connection_id, patch)
        except Exception:  # 状态记录失败不影响连接运行
            logger.exception(
                "connection state persist failed", extra={"connection_id": connection_id}
            )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(
        self, row: RowLike, config: dict[str, Any], inbound_handler: InboundHandler
    ) -> None:
        """启动目标连接的监督任务；重复启动幂等。"""
        connection_id = row.id
        if connection_id in self._tasks and not self._tasks[connection_id].done():
            return
        self._stopping.discard(connection_id)
        spec = self._registry.require_spec(row.platform)
        ctx = ConnectorContext(
            connection_id=connection_id,
            owner_user_id=row.owner_user_id,
            name=row.name,
            platform=row.platform,
            config=config,
        )
        # 先登记监督任务再执行任何 await，避免并发 start 在幂等检查与
        # 任务登记之间交错而产生孤儿任务（重复启动）。
        # 初始 starting 状态由 service 层在事务中预先落库，manager 不再独立会话
        # 重复写入，避免与请求事务争抢 SQLite 单写锁。
        self._tasks[connection_id] = asyncio.create_task(
            self._supervise(row, spec, ctx, inbound_handler),
            name=f"connection-{connection_id}",
        )

    async def stop(self, connection_id: int) -> None:
        """停止目标连接并等待监督任务退出。"""
        self._stopping.add(connection_id)
        connector = self._instances.pop(connection_id, None)
        if connector is not None:
            try:
                await connector.stop()
            except Exception:
                logger.exception("connector stop failed", extra={"connection_id": connection_id})
        task = self._tasks.get(connection_id)
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=15)
            except (TimeoutError, Exception):  # noqa: BLE001
                task.cancel()
        self._tasks.pop(connection_id, None)
        self._stopping.discard(connection_id)

    async def stop_all(self) -> None:
        for connection_id in sorted(self._tasks):
            try:
                await self.stop(connection_id)
            except Exception:
                logger.exception(
                    "connection shutdown failed", extra={"connection_id": connection_id}
                )

    async def recover_all(
        self,
        rows: list[RowLike],
        decrypt_config: Callable[[RowLike], dict[str, Any]],
        inbound_handler: InboundHandler,
    ) -> int:
        """进程启动恢复：desired_running 的连接重新拉起（docs/ARCHITECTURE §5.5）。

        与 service.start 共享两阶段顺序：
        1) 写入 starting 状态由 service 层在事务中预先落库；
        2) manager.start 只创建监督任务，不再写 starting；
        3) 启动失败时由 _supervise 走独立短会话写 error/auth_required。
        """
        started = 0
        for row in rows:
            if not row.desired_running:
                continue
            try:
                config = decrypt_config(row)
                await self.start(row, config, inbound_handler)
                started += 1
            except ConnectorError:
                logger.exception("connection recover failed", extra={"connection_id": row.id})
        return started

    # ------------------------------------------------------------------
    # 监督循环
    # ------------------------------------------------------------------

    async def _supervise(
        self,
        row: RowLike,
        spec,
        ctx: ConnectorContext,
        inbound_handler: InboundHandler,
    ) -> None:
        connection_id = row.id
        retry_count = int(row.retry_count or 0)
        while connection_id not in self._stopping:
            connector = self._registry.create(row.platform, ctx)
            if hasattr(connector, "bind_inbound"):
                connector.bind_inbound(inbound_handler)  # type: ignore[attr-defined]
            try:
                await connector.start()
            except ConnectorError as exc:
                if exc.is_auth or exc.is_config:
                    await self._record(
                        connection_id,
                        {
                            "state": ConnectionState.AUTH_REQUIRED
                            if exc.is_auth
                            else ConnectionState.ERROR,
                            "last_error_code": exc.code,
                            "last_error_message": exc.message[:500],
                            "next_retry_at": None,
                        },
                    )
                    return
                retry_count += 1
                delay = backoff_delay(retry_count - 1)
                await self._record(
                    connection_id,
                    {
                        "state": ConnectionState.ERROR,
                        "last_error_code": exc.code,
                        "last_error_message": exc.message[:500],
                        "retry_count": retry_count,
                        "next_retry_at": utc_now() + _timedelta(seconds=delay),
                    },
                )
                if await self._sleep_or_exit(connection_id, delay):
                    return
                continue

            self._instances[connection_id] = connector
            online_since = utc_now()
            await self._record(
                connection_id,
                {
                    "state": ConnectionState.ONLINE,
                    "next_retry_at": None,
                    "last_authenticated_at": online_since,
                    "last_health_at": online_since,
                },
            )

            closed_error = await self._wait_closed(connector)
            if self._instances.get(connection_id) is connector:
                self._instances.pop(connection_id, None)
            if connection_id in self._stopping:
                # 手动停止：状态由服务层负责（stopped / desired_running=false）。
                return

            # 稳定成功 60 秒后重置退避级别；期间再次断线则继承上一轮退避。
            if (utc_now() - online_since).total_seconds() >= CONNECTION_HEALTHY_RESET_SECONDS:
                retry_count = 0
            error = closed_error or (
                connector.last_error() if hasattr(connector, "last_error") else None
            )
            if error is not None and error.is_auth:
                await self._record(
                    connection_id,
                    {
                        "state": ConnectionState.AUTH_REQUIRED,
                        "last_error_code": error.code,
                        "last_error_message": error.message[:500],
                        "next_retry_at": None,
                    },
                )
                return
            message = error.message if error is not None else "连接已断开"
            code = error.code if error is not None else "network_error"
            retry_count += 1
            delay = backoff_delay(retry_count - 1)
            await self._record(
                connection_id,
                {
                    "state": ConnectionState.ERROR,
                    "last_error_code": code,
                    "last_error_message": message[:500],
                    "retry_count": retry_count,
                    "next_retry_at": utc_now() + _timedelta(seconds=delay),
                },
            )
            if await self._sleep_or_exit(connection_id, delay):
                return

    async def _wait_closed(self, connector: Connector) -> ConnectorError | None:
        try:
            await connector.wait_closed()
            return None
        except ConnectorError as exc:
            return exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ConnectorError("network_error", f"连接异常退出: {type(exc).__name__}")

    async def _sleep_or_exit(self, connection_id: int, delay: float) -> bool:
        """退避等待；期间被手动停止则返回 True 结束监督。"""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return True
        return connection_id in self._stopping


def _timedelta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


# 应用级连接管理器；lifespan 中装配并恢复。
manager = ConnectionManager()

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ..connection_config import dump_connection_config, load_connection_config
from ..dblog import log_event
from ..enums import ConnectorStatus
from ..models import IMConnection
from .base import RUNTIME_ERROR_KEY, RUNTIME_STATUS_KEY, InboundMessage, OutboundTask
from .registry import ConnectorRegistry, connector_registry

logger = logging.getLogger(__name__)


class ConnectorManager:
    def __init__(self, registry: ConnectorRegistry | None = None) -> None:
        self._connectors: dict[int, Any] = {}
        self._on_message: Callable[[InboundMessage], Awaitable[None]] | None = None
        self.registry = registry or connector_registry

    def set_on_message(self, on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self._on_message = on_message

    async def dispatch(self, message: InboundMessage) -> None:
        if self._on_message:
            await self._on_message(message)

    async def configure(
        self,
        connection: IMConnection,
        on_message: Callable[[InboundMessage], Awaitable[None]] | None = None,
    ) -> None:
        if on_message is not None:
            self._on_message = on_message
        previous = self._connectors.pop(connection.id, None)
        if previous is not None:
            try:
                await previous.stop()
            except Exception as exc:  # noqa: BLE001 - connector plugin boundary
                log_event(
                    "warning",
                    "connector",
                    f"旧连接器停止失败: {exc}",
                    {"connector_id": connection.id},
                )
        try:
            config = load_connection_config(connection.config_json)
            connector = self.registry.create(
                connection.platform,
                connection.id,
                config,
                self.dispatch,
                self._persist_state,
            )
            await connector.start(config)
            self._persist_status(connection.id, await connector.status())
        except Exception as exc:
            logger.exception("连接器 %s 启动失败", connection.id)
            self._persist_status(connection.id, ConnectorStatus.ERROR, str(exc))
            log_event(
                "error",
                "connector",
                f"连接器 {connection.id} 启动失败: {exc}",
                {"connector_id": connection.id, "platform": connection.platform.value},
            )
            return
        self._connectors[connection.id] = connector

    def get(self, connection_id: int) -> Any | None:
        return self._connectors.get(connection_id)

    def _persist_state(self, connection_id: int, state: dict[str, Any]) -> None:
        from ..db import SessionLocal

        with SessionLocal() as db:
            connection = db.get(IMConnection, connection_id)
            if connection is None:
                return
            config = load_connection_config(connection.config_json)
            runtime_status = state.pop(RUNTIME_STATUS_KEY, None)
            runtime_error = state.pop(RUNTIME_ERROR_KEY, None)
            config.update(state)
            connection.config_json = dump_connection_config(config)
            if runtime_status is not None:
                connection.status = ConnectorStatus(str(runtime_status))
            if runtime_error is not None:
                connection.last_error = str(runtime_error)
            db.commit()

    @staticmethod
    def _persist_status(
        connection_id: int,
        status: ConnectorStatus,
        error: str = "",
    ) -> None:
        from ..db import SessionLocal

        try:
            with SessionLocal() as db:
                connection = db.get(IMConnection, connection_id)
                if connection is None:
                    return
                connection.status = status
                connection.last_error = error
                db.commit()
        except SQLAlchemyError:
            # 单元测试可直接传入未持久化连接；运行态数据库错误已由调用方日志记录。
            logger.debug("连接 %s 状态未持久化", connection_id, exc_info=True)

    async def start_all(self, connections: list[IMConnection]) -> None:
        for connection in connections:
            if connection.deleted_at is None:
                await self.configure(connection)

    async def stop_all(self) -> None:
        for connection_id, connector in self._connectors.items():
            try:
                await connector.stop()
            except Exception as exc:  # noqa: BLE001 - connector plugin boundary
                log_event(
                    "warning",
                    "connector",
                    f"连接器停止失败: {exc}",
                    {"connector_id": connection_id},
                )
        self._connectors.clear()

    async def stop_connection(self, connection_id: int) -> None:
        connector = self._connectors.pop(connection_id, None)
        if connector:
            try:
                await connector.stop()
            except Exception as exc:  # noqa: BLE001 - connector plugin boundary
                log_event(
                    "warning",
                    "connector",
                    f"连接器停止失败: {exc}",
                    {"connector_id": connection_id},
                )
        self._persist_status(connection_id, ConnectorStatus.OFFLINE)

    async def send_task(self, connection: IMConnection, task: OutboundTask) -> dict[str, Any]:
        if connection.deleted_at is not None:
            raise RuntimeError("IM Bot 已删除")
        connector = self._connectors.get(connection.id)
        if connector is None:
            await self.configure(connection)
            connector = self._connectors.get(connection.id)
        if connector is None:
            raise RuntimeError(connection.last_error or "IM 连接启动失败")
        return await connector.send_task(task)

    async def health(self, connection_id: int) -> dict[str, Any]:
        connector = self._connectors.get(connection_id)
        return await connector.health() if connector else {"status": "not_started"}

    async def login(self, connection_id: int) -> dict[str, Any]:
        connector = self._connectors.get(connection_id)
        if connector is None or not hasattr(connector, "login"):
            raise RuntimeError("该平台不需要登录")
        return await connector.login()

    async def send_notice(self, connection_id: int, target: str, text: str) -> None:
        connector = self._connectors.get(connection_id)
        if connector is None:
            return
        try:
            await connector.send_task(OutboundTask(task_id="binding", text=text, target=target))
        except Exception as exc:  # noqa: BLE001 - connector failures must not break binding
            log_event(
                "warning",
                "connector.binding",
                f"绑定通知发送失败: {exc}",
                {"connector_id": connection_id},
            )

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..enums import ConnectorPlatform
from ..models import IMConnection
from .base import InboundMessage, OutboundTask
from .fake import FakeConnector
from .http_poll import HttpPollConnector
from .ilink import WeChatILinkConnector
from .sidecar import WeChatSidecarConnector
from .telegram import TelegramConnector
from .webhook import WebhookConnector
from .websocket import WebSocketConnector
from .wecom import WeComConnector

logger = logging.getLogger(__name__)


class ConnectorManager:
    def __init__(self) -> None:
        self._connectors: dict[int, Any] = {}
        self._on_message: Callable[[InboundMessage], Awaitable[None]] | None = None

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
        config = json.loads(connection.config_json or "{}")
        if connection.platform is ConnectorPlatform.TELEGRAM:
            connector = TelegramConnector(connection.id, config, self.dispatch)
        elif connection.platform is ConnectorPlatform.WECOM:
            connector = WeComConnector(connection.id, config)
        elif connection.platform is ConnectorPlatform.WECHAT_ILINK:
            connector = WeChatILinkConnector(
                connection.id, config, self.dispatch, on_state=self._persist_state
            )
        elif connection.platform is ConnectorPlatform.WEBHOOK:
            connector = WebhookConnector(connection.id, config)
        elif connection.platform is ConnectorPlatform.HTTP:
            connector = HttpPollConnector(connection.id, config, self.dispatch)
        elif connection.platform is ConnectorPlatform.WEBSOCKET:
            connector = WebSocketConnector(connection.id, config)
        elif connection.platform is ConnectorPlatform.WECHAT_SIDECAR:
            connector = WeChatSidecarConnector()
        else:
            connector = FakeConnector()
        previous = self._connectors.pop(connection.id, None)
        if previous is not None:
            await previous.stop()
        try:
            await connector.start(config)
        except Exception:
            logger.exception("连接器 %s 启动失败", connection.id)
        self._connectors[connection.id] = connector

    def get(self, connection_id: int) -> Any | None:
        return self._connectors.get(connection_id)

    def _persist_state(self, connection_id: int, state: dict[str, Any]) -> None:
        from ..db import SessionLocal

        with SessionLocal() as db:
            connection = db.get(IMConnection, connection_id)
            if connection is None:
                return
            try:
                config = json.loads(connection.config_json or "{}")
            except json.JSONDecodeError:
                config = {}
            config.update(state)
            connection.config_json = json.dumps(config, ensure_ascii=False)
            db.commit()

    async def start_all(self, connections: list[IMConnection]) -> None:
        for connection in connections:
            await self.configure(connection)

    async def stop_all(self) -> None:
        for connector in self._connectors.values():
            await connector.stop()
        self._connectors.clear()

    async def stop_connection(self, connection_id: int) -> None:
        connector = self._connectors.pop(connection_id, None)
        if connector:
            await connector.stop()

    async def send_task(self, connection: IMConnection, task: OutboundTask) -> dict[str, Any]:
        connector = self._connectors.get(connection.id)
        if connector is None:
            await self.configure(connection)
            connector = self._connectors[connection.id]
        return await connector.send_task(task)

    async def health(self, connection_id: int) -> dict[str, Any]:
        connector = self._connectors.get(connection_id)
        return await connector.health() if connector else {"status": "not_started"}

    async def login(self, connection_id: int) -> dict[str, Any]:
        connector = self._connectors.get(connection_id)
        if not hasattr(connector, "login"):
            raise RuntimeError("该平台不需要登录")
        return await connector.login()

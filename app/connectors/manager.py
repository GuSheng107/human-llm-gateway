import json
from collections.abc import Awaitable, Callable
from typing import Any

from .base import InboundMessage, OutboundTask
from .fake import FakeConnector
from .telegram import TelegramConnector
from .wecom import WeComConnector
from .sidecar import WeChatSidecarConnector
from ..enums import ConnectorPlatform
from ..models import IMConnection


class ConnectorManager:
    def __init__(self) -> None:
        self._connectors: dict[int, Any] = {}
        self._on_message: Callable[[InboundMessage], Awaitable[None]] | None = None

    def set_on_message(self, on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self._on_message = on_message

    async def configure(self, connection: IMConnection,
                        on_message: Callable[[InboundMessage], Awaitable[None]] | None = None) -> None:
        if on_message is not None:
            self._on_message = on_message
        config = json.loads(connection.config_json or "{}")
        if connection.platform is ConnectorPlatform.TELEGRAM:
            connector = TelegramConnector(connection.id, config, self._on_message or self._ignore)
        elif connection.platform is ConnectorPlatform.WECOM:
            connector = WeComConnector(connection.id, config)
        elif connection.platform is ConnectorPlatform.WECHAT_SIDECAR:
            connector = WeChatSidecarConnector()
        else:
            connector = FakeConnector()
        await connector.start(config)
        self._connectors[connection.id] = connector

    async def stop_all(self) -> None:
        for connector in self._connectors.values():
            await connector.stop()
        self._connectors.clear()

    async def send_task(self, connection: IMConnection, task: OutboundTask) -> dict[str, Any]:
        connector = self._connectors.get(connection.id)
        if connector is None:
            await self.configure(connection)
            connector = self._connectors[connection.id]
        return await connector.send_task(task)

    async def health(self, connection_id: int) -> dict[str, Any]:
        connector = self._connectors.get(connection_id)
        return await connector.health() if connector else {"status": "not_started"}

    async def _ignore(self, _: InboundMessage) -> None:
        return None

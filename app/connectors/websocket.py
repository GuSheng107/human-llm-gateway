import asyncio
import json
from typing import Any

from fastapi import WebSocket

from ..enums import ConnectorStatus
from .base import OutboundTask


class WebSocketConnector:
    """服务端模式 WebSocket 通道:外部客户端连到
    /connectors/ws/{connector_id}?token=...;收文本/JSON 帧;出站广播给在线 socket。
    """

    platform = "websocket"

    def __init__(self, connector_id: int, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = dict(config)
        self._status = ConnectorStatus.OFFLINE
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        self._status = (
            ConnectorStatus.OFFLINE if not self.config.get("auth_token") else ConnectorStatus.ONLINE
        )

    async def stop(self) -> None:
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return ConnectorStatus.ONLINE if self._clients else self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "connected": len(self._clients),
            "inbound_url": f"/connectors/ws/{self.connector_id}",
        }

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        if not self._clients:
            raise RuntimeError("websocket 通道当前无在线客户端")
        payload = json.dumps(
            {"task_id": task.task_id, "text": task.text, "target": task.target},
            ensure_ascii=False,
        )
        sent = 0
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception:
                await self.unregister(ws)
        return {"accepted": sent > 0, "sent_to": sent}

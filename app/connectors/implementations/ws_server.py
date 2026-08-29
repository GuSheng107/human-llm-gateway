"""自定义 WebSocket 连接器：带连接 Token 的双向服务端会话。"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from ...domain.connections import ERROR_DELIVERY, ConnectorError
from ..base import Connector, ConnectorContext, DeliveryEnvelope
from ..registry import ConfigField, PlatformSpec


class WsJsonSession(Protocol):
    """WebSocket 会话最小协议（由 API 层适配 Starlette WebSocket）。"""

    async def send_json(self, data: dict[str, Any]) -> None: ...


class WebSocketServerConnector(Connector):
    """维护该连接的全部活动 WS 会话；deliver 向所有会话推送任务包。"""

    platform = "websocket"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._sessions: dict[int, WsJsonSession] = {}
        self._next_session_id = 0
        self._closed = asyncio.Event()
        self._lock = asyncio.Lock()

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not config.get("connection_token"):
            problems.append("缺少连接 Token")
        return problems

    async def start(self) -> None:
        self._closed.clear()

    async def stop(self) -> None:
        self._closed.set()
        async with self._lock:
            self._sessions.clear()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    def verify_token(self, token: str | None) -> bool:
        import hmac

        expected = str(self.ctx.config.get("connection_token") or "")
        return bool(expected) and hmac.compare_digest(expected, token or "")

    async def register_session(self, session: WsJsonSession) -> int:
        async with self._lock:
            self._next_session_id += 1
            self._sessions[self._next_session_id] = session
            return self._next_session_id

    async def remove_session(self, session_id: int) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        async with self._lock:
            sessions = list(self._sessions.items())
        if not sessions:
            raise ConnectorError(ERROR_DELIVERY, "没有在线的 WebSocket 会话")
        payload = envelope.to_json()
        dead: list[int] = []
        for session_id, session in sessions:
            try:
                await session.send_json(payload)
            except Exception:  # noqa: BLE001  # 会话已断开时记录并清理
                dead.append(session_id)
        for session_id in dead:
            await self.remove_session(session_id)
        if len(dead) == len(sessions):
            raise ConnectorError(ERROR_DELIVERY, "WebSocket 会话全部推送失败")

    async def health(self) -> dict[str, Any]:
        async with self._lock:
            return {"running": not self._closed.is_set(), "sessions": len(self._sessions)}


WEBSOCKET_SPEC = PlatformSpec(
    code="websocket",
    label="自定义 WebSocket",
    description="带连接 Token 的双向 WebSocket 会话。",
    kind="server",
    supports_delivery=True,
    config_fields=(
        ConfigField(name="connection_token", label="连接 Token", required=True, secret=True),
    ),
)

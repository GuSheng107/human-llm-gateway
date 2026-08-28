from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..enums import ConnectorStatus
from .base import (
    RUNTIME_ERROR_KEY,
    RUNTIME_STATUS_KEY,
    InboundMessage,
    OutboundTask,
    StateHandler,
)

try:
    from wecom_aibot_sdk import WSClient
except ImportError:  # pragma: no cover - 启动时给出明确依赖错误
    WSClient = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _normalized_key(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return ""


def _find_string(data: Any, keys: set[str], depth: int = 0) -> str:
    if depth > 8:
        return ""
    if isinstance(data, dict):
        normalized = {_normalized_key(key): key for key in data}
        for key in keys:
            original = normalized.get(_normalized_key(key))
            if original is not None:
                value = _string_value(data.get(original))
                if value:
                    return value
        for value in data.values():
            found = _find_string(value, keys, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_string(value, keys, depth + 1)
            if found:
                return found
    return ""


def extract_wecom_message(connector_id: int, frame: dict[str, Any]) -> InboundMessage | None:
    body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
    text_node = body.get("text") if isinstance(body.get("text"), dict) else {}
    content = _string_value(text_node.get("content"))
    if not content:
        return None
    sender_id = _find_string(
        body,
        {
            "from",
            "userid",
            "user_id",
            "sender_id",
            "from_userid",
            "open_userid",
            "external_userid",
        },
    )
    if not sender_id:
        return None
    conversation_id = (
        _find_string(
            body,
            {"chatid", "chat_id", "conversation_id", "roomid", "room_id"},
        )
        or sender_id
    )
    headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
    external_message_id = _find_string(
        body,
        {"msgid", "msg_id", "message_id", "messageid"},
    ) or _string_value(headers.get("req_id"))
    return InboundMessage(
        connector_id=connector_id,
        sender_id=sender_id,
        text=content,
        conversation_id=conversation_id,
        external_message_id=external_message_id,
    )


class WeComConnector:
    """企业微信 AI Bot SDK 长连接；一个连接实例属于一个系统用户。"""

    platform = "wecom"

    def __init__(
        self,
        connector_id: int,
        config: dict[str, Any],
        on_message: Callable[[InboundMessage], Awaitable[None]],
        on_state: StateHandler | None = None,
    ) -> None:
        self.connector_id = connector_id
        self.config = dict(config)
        self._on_message = on_message
        self._on_state = on_state
        self._status = ConnectorStatus.OFFLINE
        self._error = ""
        self._client: Any | None = None
        self._authenticated = asyncio.Event()

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        if WSClient is None:
            raise RuntimeError("缺少 wecom-aibot-sdk，请先执行 uv sync --locked")
        bot_id = str(self.config.get("bot_id", "")).strip()
        secret = str(self.config.get("secret", "")).strip()
        if not bot_id or not secret:
            raise RuntimeError("企业微信连接缺少 bot_id 或 secret")

        kwargs: dict[str, Any] = {
            "bot_id": bot_id,
            "secret": secret,
            "reconnect_interval": int(self.config.get("reconnect_interval_ms", 5_000)),
            "heartbeat_interval": int(self.config.get("heartbeat_interval_ms", 30_000)),
        }
        websocket_url = str(self.config.get("websocket_url", "")).strip()
        if websocket_url:
            kwargs["ws_url"] = websocket_url

        self._status = ConnectorStatus.CONNECTING
        self._error = ""
        self._authenticated.clear()
        self._client = WSClient(**kwargs)
        self._client.on("authenticated", self._on_authenticated)
        self._client.on("message", self._on_frame)
        self._client.on("error", self._on_error)
        await self._client.connect()

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "authenticated": self._authenticated.is_set(),
            "configured": bool(self.config.get("bot_id") and self.config.get("secret")),
            "error": self._error,
        }

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        if self._client is None or not self._authenticated.is_set():
            raise RuntimeError("企业微信 Bot 尚未完成长连接认证")
        target = task.target or str(self.config.get("chat_id", ""))
        if not target:
            raise RuntimeError("企业微信 Bot 尚未绑定操作者 userid")
        result = await self._client.send_message(
            target,
            {"msgtype": "markdown", "markdown": {"content": task.text}},
        )
        return {"accepted": True, "response": result}

    async def _on_authenticated(self, *_args: Any, **_kwargs: Any) -> None:
        self._authenticated.set()
        self._status = ConnectorStatus.ONLINE
        self._persist(
            {
                "authenticated": True,
                RUNTIME_STATUS_KEY: ConnectorStatus.ONLINE.value,
                RUNTIME_ERROR_KEY: "",
            }
        )

    async def _on_frame(self, frame: dict[str, Any]) -> None:
        message = extract_wecom_message(self.connector_id, frame)
        if message is not None:
            await self._on_message(message)

    async def _on_error(self, error: Exception) -> None:
        self._error = str(error)
        self._status = ConnectorStatus.ERROR
        logger.error("企业微信连接 %s 异常: %s", self.connector_id, error)
        self._persist(
            {
                "authenticated": False,
                RUNTIME_STATUS_KEY: ConnectorStatus.ERROR.value,
                RUNTIME_ERROR_KEY: self._error,
            }
        )

    def _persist(self, state: dict[str, Any]) -> None:
        if self._on_state is not None:
            self._on_state(self.connector_id, state)

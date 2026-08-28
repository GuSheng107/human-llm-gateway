import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..enums import ConnectorStatus
from .base import (
    RUNTIME_ERROR_KEY,
    RUNTIME_STATUS_KEY,
    InboundMessage,
    OutboundTask,
    StateHandler,
)

logger = logging.getLogger(__name__)


class HttpPollConnector:
    """通用 HTTP 轮询通道:网关定时 GET inbound_url 拉取消息;出站 POST target_url。

    进站响应格式:单个对象 {"sender_id","text",...} 或 {"messages":[...]}。
    """

    platform = "http"

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
        self._poll_task: asyncio.Task[None] | None = None
        self._last_error = ""

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        if self.config.get("inbound_url"):
            self._status = ConnectorStatus.ONLINE
            if not (self._poll_task and not self._poll_task.done()):
                self._poll_task = asyncio.create_task(self._poll_loop())
        elif self.config.get("target_url"):
            self._status = ConnectorStatus.ONLINE
        else:
            self._status = ConnectorStatus.OFFLINE

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "polling": bool(self._poll_task and not self._poll_task.done()),
            "outbound_configured": bool(self.config.get("target_url")),
            "cursor": str(self.config.get("cursor", "")),
            "error": self._last_error,
        }

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        url = str(self.config.get("target_url", ""))
        if not url:
            raise RuntimeError("http 连接未配置 target_url,无法出站")
        headers = dict(self.config.get("headers", {}))
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"task_id": task.task_id, "text": task.text, "target": task.target},
            )
            response.raise_for_status()
        return {"accepted": True}

    @staticmethod
    def extract_messages(body: Any) -> list[InboundMessage]:
        if not isinstance(body, dict):
            return []
        raw = body.get("messages", [body])
        items: list[InboundMessage] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            items.append(
                InboundMessage(
                    connector_id=0,
                    sender_id=str(item.get("sender_id", "")),
                    text=str(item["text"]),
                    conversation_id=str(item.get("conversation_id", "")),
                    external_message_id=str(item.get("external_message_id", "")),
                    reply_to_task_id=(
                        str(item["reply_to_task_id"]) if item.get("reply_to_task_id") else None
                    ),
                )
            )
        return items

    async def _poll_loop(self) -> None:
        url = str(self.config.get("inbound_url", ""))
        headers = dict(self.config.get("headers", {}))
        interval = float(self.config.get("poll_interval_seconds", 5))
        cursor_param = str(self.config.get("cursor_param", "cursor"))
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                while True:
                    try:
                        cursor = str(self.config.get("cursor", ""))
                        params = {cursor_param: cursor} if cursor else None
                        response = await client.get(url, headers=headers, params=params)
                        response.raise_for_status()
                        body = response.json()
                        handled_ids: list[str] = []
                        for message in self.extract_messages(body):
                            message = InboundMessage(
                                connector_id=self.connector_id,
                                sender_id=message.sender_id,
                                text=message.text,
                                conversation_id=message.conversation_id,
                                external_message_id=message.external_message_id,
                                reply_to_task_id=message.reply_to_task_id,
                            )
                            await self._on_message(message)
                            if message.external_message_id:
                                handled_ids.append(message.external_message_id)
                        await self._ack(client, headers, handled_ids)
                        next_cursor = str(
                            body.get("next_cursor", body.get("cursor", ""))
                            if isinstance(body, dict)
                            else ""
                        )
                        state: dict[str, Any] = {
                            RUNTIME_STATUS_KEY: ConnectorStatus.ONLINE.value,
                            RUNTIME_ERROR_KEY: "",
                        }
                        if next_cursor and next_cursor != cursor:
                            self.config["cursor"] = next_cursor
                            state["cursor"] = next_cursor
                        self._last_error = ""
                        self._status = ConnectorStatus.ONLINE
                        if self._on_state is not None:
                            self._on_state(self.connector_id, state)
                    except asyncio.CancelledError:
                        raise
                    except (httpx.HTTPError, ValueError, TypeError) as exc:
                        self._last_error = str(exc)
                        self._status = ConnectorStatus.ERROR
                        logger.warning("HTTP 轮询连接 %s 失败: %s", self.connector_id, exc)
                        if self._on_state is not None:
                            self._on_state(
                                self.connector_id,
                                {
                                    RUNTIME_STATUS_KEY: ConnectorStatus.ERROR.value,
                                    RUNTIME_ERROR_KEY: self._last_error,
                                },
                            )
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _ack(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        message_ids: list[str],
    ) -> None:
        ack_url = str(self.config.get("ack_url", ""))
        if not ack_url or not message_ids:
            return
        response = await client.post(
            ack_url,
            headers=headers,
            json={"connector_id": self.connector_id, "message_ids": message_ids},
        )
        response.raise_for_status()

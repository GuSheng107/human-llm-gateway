import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..enums import ConnectorStatus
from .base import InboundMessage, OutboundTask


class TelegramConnector:
    """Small, dependency-light Telegram Bot API connector.

    The polling loop is intentionally owned by the connector manager so one
    database connection maps to one bot token and one API key.
    """

    def __init__(self, connector_id: int, config: dict[str, Any],
                 on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self.connector_id = connector_id
        self.config = config
        self.on_message = on_message
        self._status = ConnectorStatus.OFFLINE
        self._stop = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._offset = 0

    @property
    def token(self) -> str:
        return str(self.config.get("bot_token", ""))

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config = config
        if not self.token:
            self._status = ConnectorStatus.ERROR
            return
        self._stop.clear()
        self._status = ConnectorStatus.ONLINE
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {"status": self._status.value, "configured": bool(self.token), "platform": "telegram"}

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        chat_id = task.target or str(self.config.get("chat_id", ""))
        if not self.token or not chat_id:
            raise RuntimeError("Telegram connector requires bot_token and chat_id")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.base_url}/sendMessage",
                                         json={"chat_id": chat_id, "text": task.text})
            response.raise_for_status()
            body = response.json()
        return {"accepted": bool(body.get("ok")), "external_id": str(body.get("result", {}).get("message_id", ""))}

    async def _poll_loop(self) -> None:
        interval = float(self.config.get("poll_interval_seconds", 2))
        try:
            async with httpx.AsyncClient(timeout=max(10, interval + 10)) as client:
                while not self._stop.is_set():
                    try:
                        response = await client.get(f"{self.base_url}/getUpdates",
                                                    params={"timeout": max(1, int(interval + 5)), "offset": self._offset})
                        response.raise_for_status()
                        body = response.json()
                        if not body.get("ok"):
                            self._status = ConnectorStatus.ERROR
                        for update in body.get("result", []):
                            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                            message = update.get("message") or {}
                            text = message.get("text")
                            if not text:
                                continue
                            await self.on_message(InboundMessage(
                                connector_id=self.connector_id,
                                sender_id=str(message.get("from", {}).get("id", "")),
                                conversation_id=str(message.get("chat", {}).get("id", "")),
                                external_message_id=str(message.get("message_id", "")),
                                text=text,
                            ))
                        await asyncio.sleep(0)
                    except asyncio.CancelledError:
                        raise
                    except (httpx.HTTPError, ValueError, KeyError):
                        self._status = ConnectorStatus.ERROR
                        await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass


import json
from typing import Any

import httpx

from ..enums import ConnectorStatus
from .base import InboundMessage, OutboundTask


class WeComConnector:
    """Enterprise WeChat connector for webhook/API mode.

    Inbound callback verification and optional WebSocket SDK hosting remain at
    the sidecar boundary; the core endpoint accepts normalized inbound events.
    Outbound webhook delivery is implemented here and is safe to test with a
    fake HTTP transport.
    """

    def __init__(self, connector_id: int, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config
        self._status = ConnectorStatus.ONLINE if config.get("webhook_url") else ConnectorStatus.OFFLINE

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config = config
        self._status = ConnectorStatus.ONLINE if self.config.get("webhook_url") else ConnectorStatus.OFFLINE

    async def stop(self) -> None:
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {"status": self._status.value, "configured": bool(self.config.get("webhook_url")),
                "platform": "wecom", "inbound": "normalized-callback"}

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        webhook = str(self.config.get("webhook_url", ""))
        if not webhook:
            raise RuntimeError("WeCom connector requires webhook_url")
        payload = {"msgtype": "text", "text": {"content": task.text}}
        if self.config.get("mentioned_list"):
            payload["text"]["mentioned_list"] = self.config["mentioned_list"]
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(webhook, json=payload)
            response.raise_for_status()
            body = response.json()
        return {"accepted": body.get("errcode", -1) == 0, "response": body}


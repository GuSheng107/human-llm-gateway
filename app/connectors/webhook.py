from typing import Any

import httpx

from ..enums import ConnectorStatus
from .base import OutboundTask


class WebhookConnector:
    """通用 Webhook 通道:外部系统 POST 进站;出站任务 POST 到 target_url。

    进站鉴权:请求头 X-Connector-Token == config["inbound_token"]。
    """

    platform = "webhook"

    def __init__(self, connector_id: int, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = dict(config)
        self._status = ConnectorStatus.OFFLINE

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        usable = bool(self.config.get("inbound_token") or self.config.get("target_url"))
        self._status = ConnectorStatus.ONLINE if usable else ConnectorStatus.OFFLINE

    async def stop(self) -> None:
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "inbound_configured": bool(self.config.get("inbound_token")),
            "outbound_configured": bool(self.config.get("target_url")),
        }

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        url = str(self.config.get("target_url", ""))
        if not url:
            raise RuntimeError("webhook 连接未配置 target_url,无法出站")
        headers = dict(self.config.get("headers", {}))
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"task_id": task.task_id, "text": task.text, "target": task.target},
            )
            response.raise_for_status()
        return {"accepted": True}

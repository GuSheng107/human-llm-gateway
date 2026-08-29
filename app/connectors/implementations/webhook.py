"""自定义 Webhook 连接器：服务端接收入站，按配置推送出站。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ...domain.connections import ERROR_AUTH, ERROR_DELIVERY, ERROR_NETWORK, ConnectorError
from ..base import Connector, ConnectorContext, DeliveryEnvelope


class WebhookConnector(Connector):
    """服务端型连接器：进站由 API 层处理，实例负责按配置推送。"""

    platform = "webhook"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._client: httpx.AsyncClient | None = None
        self._closed = asyncio.Event()

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not config.get("inbound_token"):
            problems.append("缺少入站 Token")
        url = config.get("outbound_url")
        if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
            problems.append("推送 URL 必须是 http(s) 地址")
        return problems

    async def start(self) -> None:
        self._closed.clear()
        self._client = httpx.AsyncClient(timeout=10)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._closed.set()

    async def wait_closed(self) -> None:
        # 服务端型连接器在 stop 之前保持在线，不参与退避重连。
        await self._closed.wait()

    async def health(self) -> dict[str, Any]:
        return {"running": not self._closed.is_set()}

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        client = self._client
        if client is None:
            raise ConnectorError(ERROR_DELIVERY, "连接未启动，无法推送")
        token = self.ctx.config.get("outbound_token") or ""
        headers = {"X-Gateway-Token": token} if token else {}
        try:
            response = await client.post(
                self.ctx.config["outbound_url"], json=envelope.to_json(), headers=headers
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(ERROR_NETWORK, f"Webhook 推送失败: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise ConnectorError(ERROR_AUTH, "Webhook 推送被拒绝")
        if response.status_code >= 400:
            raise ConnectorError(ERROR_DELIVERY, f"Webhook 推送返回 {response.status_code}")

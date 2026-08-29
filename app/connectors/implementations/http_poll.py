"""自定义 HTTP 轮询连接器：cursor 拉取任务、提交回复、可选 ACK。

任务包持久化在 connector_outbox（pending），由外部客户端通过
/connectors/http/{id}/tasks 按 cursor 拉取；连接器本身不做网络动作。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..base import Connector, ConnectorContext
from ..registry import ConfigField, PlatformSpec


class HttpPollConnector(Connector):
    platform = "http_poll"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._closed = asyncio.Event()

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not config.get("pull_token"):
            problems.append("缺少拉取 Token")
        return problems

    async def start(self) -> None:
        self._closed.clear()

    async def stop(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        # 轮询型连接器在 stop 之前保持在线。
        await self._closed.wait()

    def verify_token(self, token: str | None) -> bool:
        import hmac

        expected = str(self.ctx.config.get("pull_token") or "")
        return bool(expected) and hmac.compare_digest(expected, token or "")

    async def health(self) -> dict[str, Any]:
        return {"running": not self._closed.is_set()}


HTTP_POLL_SPEC = PlatformSpec(
    code="http_poll",
    label="自定义 HTTP 轮询",
    description="按 cursor 拉取任务、提交回复和可选 ACK。",
    kind="server",
    supports_delivery=False,
    config_fields=(ConfigField(name="pull_token", label="拉取 Token", required=True, secret=True),),
)

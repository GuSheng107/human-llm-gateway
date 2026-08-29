"""企业微信智能机器人连接器：基于 wecom-aibot-sdk 的 WebSocket 长连接。

SDK 自带重连，但认证失败必须停止自动重试并等待所有者处理，因此由
本连接器把 SDK 的连接/认证事件桥接为统一状态；认证失败映射为
auth_required。SDK 异常统一分类为脱敏连接错误，不暴露内部细节。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...domain.connections import ERROR_AUTH, ERROR_DELIVERY, ERROR_NETWORK, ConnectorError
from ..base import Connector, ConnectorContext, DeliveryEnvelope

logger = logging.getLogger(__name__)


def _classify(exc: Exception) -> ConnectorError:
    from wecom_aibot_sdk import WSAuthFailureError

    if isinstance(exc, WSAuthFailureError):
        return ConnectorError(ERROR_AUTH, "企微认证失败，请检查 Bot 配置")
    text = type(exc).__name__
    return ConnectorError(ERROR_NETWORK, f"企微连接错误: {text}")


class WeComAibotConnector(Connector):
    platform = "wecom_aibot"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._client: Any | None = None
        self._task: asyncio.Task | None = None
        self._closed = asyncio.Event()
        self._last_error: ConnectorError | None = None
        self._inbound = None

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not config.get("bot_id"):
            problems.append("缺少 Bot ID")
        if not config.get("secret"):
            problems.append("缺少 Bot Secret")
        return problems

    def bind_inbound(self, callback) -> None:
        self._inbound = callback

    async def start(self) -> None:
        from wecom_aibot_sdk import WSClient

        if self._task is not None and not self._task.done():
            return
        self._closed.clear()
        self._last_error = None
        self._client = WSClient(
            bot_id=str(self.ctx.config["bot_id"]),
            secret=str(self.ctx.config["secret"]),
        )
        self._task = asyncio.create_task(self._run(), name=f"wecom-aibot-{self.ctx.connection_id}")

    async def _run(self) -> None:
        client = self._client
        assert client is not None
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001
            self._last_error = _classify(exc)
            self._closed.set()
            return
        # connect() 成功后 SDK 内部维护会话；此处等待 stop 或连接失败。
        try:
            await self._closed.wait()
        finally:
            try:
                await client.disconnect()
            except Exception:  # 关闭失败不阻塞停止流程
                logger.info("wecom disconnect failed", exc_info=True)

    async def wait_closed(self) -> None:
        if self._task is not None:
            await self._task

    def last_error(self) -> ConnectorError | None:
        return self._last_error

    async def stop(self) -> None:
        self._closed.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, Exception):  # noqa: BLE001
                self._task.cancel()
            self._task = None
        self._client = None

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        client = self._client
        if client is None or not getattr(client, "is_connected", lambda: True)():
            raise ConnectorError(ERROR_DELIVERY, "企微连接不在线")
        target = envelope.reply_to_external_id or ""
        if not target:
            raise ConnectorError(ERROR_DELIVERY, "缺少投递目标")
        try:
            await client.send_message(target, envelope.prompt_text)
        except Exception as exc:
            raise _classify(exc) from exc

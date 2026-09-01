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
from ..base import Connector, ConnectorContext, DeliveryEnvelope, InboundMessage

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
        self._client.on("message.text", self._handle_text_message)
        self._task = asyncio.create_task(self._run(), name=f"wecom-aibot-{self.ctx.connection_id}")

    async def _handle_text_message(self, frame: Any) -> None:
        """把企微文本消息桥接到统一入站流程；未绑定时文本即绑定码。"""
        if self._inbound is None:
            return
        body = frame.get("body", {}) if isinstance(frame, dict) else getattr(frame, "body", {})
        headers = (
            frame.get("headers", {}) if isinstance(frame, dict) else getattr(frame, "headers", {})
        )
        sender = str((body.get("from") or {}).get("userid") or "").strip()
        text = str((body.get("text") or {}).get("content") or "").strip()
        external_id = str(body.get("msgid") or headers.get("req_id") or "").strip()
        chat_id = str(body.get("chatid") or "").strip()
        chat_type = str(body.get("chattype") or "").strip().lower()
        if not sender or not text or not external_id:
            return
        # 单聊帧经常缺少 chatid 或使用与 userid 不同的会话标识：
        # 群聊才以 chatid 为准；其余一律视为与发送者的个人会话。
        is_group_chat = chat_type in {"group", "room"}
        if not is_group_chat and not chat_id:
            chat_id = sender
        is_personal_chat = not is_group_chat and (not chat_id or chat_id == sender)
        result = await self._inbound(
            self.ctx.connection_id,
            InboundMessage(
                external_message_id=external_id,
                sender_external_id=sender,
                text=text,
                binding_code=text if is_personal_chat else None,
                raw={
                    "chatid": chat_id,
                    "chattype": chat_type,
                },
            ),
        )
        result_value = getattr(result, "value", result)
        if result_value == "bound" and self._client is not None:
            await self._client.reply(
                frame,
                {"msgtype": "text", "text": {"content": "连接绑定成功，可以开始接收任务。"}},
            )
        elif text == "connect mycom" and not is_personal_chat and self._client is not None:
            await self._client.reply(
                frame,
                {
                    "msgtype": "text",
                    "text": {"content": "绑定失败，请在与机器人的个人会话中发送 connect mycom。"},
                },
            )

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
            await client.send_message(
                target,
                {"msgtype": "markdown", "markdown": {"content": envelope.prompt_text}},
            )
        except Exception as exc:
            raise _classify(exc) from exc

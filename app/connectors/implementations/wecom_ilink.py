"""微信 iLink 连接器：基于 openilink SDK 的长轮询监听与消息发送。

SDK 是同步实现，在线程中运行 monitor 长轮询循环；进站消息通过
run_coroutine_threadsafe 桥接到异步进站回调。会话过期（扫码失效）
映射为 auth_required，由连接管理器停止重试并等待所有者重新扫码。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from ...domain.connections import ERROR_AUTH, ERROR_DELIVERY, ERROR_NETWORK, ConnectorError
from ..base import Connector, ConnectorContext, DeliveryEnvelope, InboundMessage

logger = logging.getLogger(__name__)


def _classify(exc: Exception) -> ConnectorError:
    """把 SDK 异常归类为脱敏连接错误。"""
    text = type(exc).__name__
    if getattr(exc, "is_session_expired", False):
        return ConnectorError(ERROR_AUTH, "iLink 会话已过期，请重新扫码登录")
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status in (401, 403):
        return ConnectorError(ERROR_AUTH, "iLink 认证被拒绝")
    return ConnectorError(ERROR_NETWORK, f"iLink 网络错误: {text}")


class WeComIlinkConnector(Connector):
    platform = "wecom_ilink"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._client: Any | None = None
        self._thread: threading.Thread | None = None
        self._thread_error: ConnectorError | None = None
        self._closed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbound = None  # InboundCallback
        self._pending_qrcode: str | None = None

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        # token 允许为空：扫码登录成功后再写回；缺失 token 仅影响 start。
        return []

    def bind_inbound(self, callback) -> None:
        self._inbound = callback

    async def start(self) -> None:
        from openilink import Client, MonitorOptions

        if self._thread is not None and self._thread.is_alive():
            return
        self._closed.clear()
        self._thread_error = None
        self._loop = asyncio.get_running_loop()
        config = self.ctx.config
        token = str(config.get("token") or "")
        if not token:
            raise ConnectorError(ERROR_AUTH, "缺少 iLink Token，请先扫码登录")
        kwargs: dict[str, Any] = {"token": token}
        if config.get("base_url"):
            kwargs["base_url"] = str(config["base_url"])
        self._client = Client(**kwargs)

        def _on_session_expired() -> None:
            self._thread_error = ConnectorError(ERROR_AUTH, "iLink 会话已过期，请重新扫码登录")

        def _on_error(exc: Exception) -> None:
            logger.warning(
                "ilink monitor error",
                extra={"connection_id": self.ctx.connection_id, "error": type(exc).__name__},
            )

        def _run() -> None:
            try:
                self._client.monitor(
                    self._handle_sdk_message,
                    MonitorOptions(on_session_expired=_on_session_expired, on_error=_on_error),
                )
            except Exception as exc:  # noqa: BLE001  # 线程内兜底分类
                self._thread_error = _classify(exc)
            finally:
                self._closed.set()

        self._thread = threading.Thread(
            target=_run, name=f"ilink-{self.ctx.connection_id}", daemon=True
        )
        self._thread.start()

    def _handle_sdk_message(self, message: Any) -> None:
        """SDK 线程回调：把 WeixinMessage 桥接为统一进站消息。"""
        if self._inbound is None or self._loop is None:
            return
        from openilink import extract_text

        external_id = str(getattr(message, "message_id", "") or "")
        sender = str(getattr(message, "from_user_id", "") or "")
        text = extract_text(message)
        context_token = getattr(message, "context_token", None)
        future = asyncio.run_coroutine_threadsafe(
            self._inbound(
                self.ctx.connection_id,
                InboundMessage(
                    external_message_id=external_id,
                    sender_external_id=sender,
                    text=text,
                    raw={"context_token": context_token} if context_token else {},
                ),
            ),
            self._loop,
        )
        try:
            future.result(timeout=10)
        except Exception:  # 不让进站异常终止监听线程
            logger.exception("ilink inbound handling failed")

    async def wait_closed(self) -> None:
        if self._thread is None:
            return
        await asyncio.to_thread(self._closed.wait)

    def last_error(self) -> ConnectorError | None:
        return self._thread_error

    async def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:  # 关闭失败不阻塞停止流程
                logger.warning("ilink stop failed", exc_info=True)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, timeout=10)
            self._thread = None
        self._client = None
        self._closed.set()

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        client = self._client
        if client is None or self._thread is None or not self._thread.is_alive():
            raise ConnectorError(ERROR_DELIVERY, "iLink 连接不在线")
        target = envelope.reply_to_external_id or ""
        if not target:
            raise ConnectorError(ERROR_DELIVERY, "缺少投递目标")
        try:
            await asyncio.to_thread(client.push, target, envelope.prompt_text)
        except Exception as exc:
            raise _classify(exc) from exc

    async def start_login(self) -> dict[str, Any]:
        from openilink import Client

        client = self._client
        if client is None:
            kwargs: dict[str, Any] = {"token": str(self.ctx.config.get("token") or "")}
            if self.ctx.config.get("base_url"):
                kwargs["base_url"] = str(self.ctx.config["base_url"])
            client = Client(**kwargs)
            self._client = client
        try:
            response = await asyncio.to_thread(client.fetch_qr_code)
        except Exception as exc:
            raise _classify(exc) from exc
        self._pending_qrcode = getattr(response, "qrcode", None)
        return {
            "qrcode": getattr(response, "qrcode", ""),
            "qrcode_img_content": getattr(response, "qrcode_img_content", b""),
        }

    async def poll_login(self) -> dict[str, Any]:
        client = self._client
        if client is None or not self._pending_qrcode:
            raise ConnectorError(ERROR_DELIVERY, "尚未发起扫码登录")
        try:
            response = await asyncio.to_thread(client.poll_qr_status, self._pending_qrcode)
        except Exception as exc:
            raise _classify(exc) from exc
        status = getattr(response, "status", "")
        result: dict[str, Any] = {"status": status}
        if status == "confirmed":
            result["bot_token"] = getattr(response, "bot_token", "")
            result["baseurl"] = getattr(response, "baseurl", "")
            result["ilink_user_id"] = getattr(response, "ilink_user_id", "")
            result["ilink_bot_id"] = getattr(response, "ilink_bot_id", "")
            self._pending_qrcode = None
        return result

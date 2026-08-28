import asyncio
import base64
import io
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import httpx  # noqa: F401
import qrcode
import qrcode.image.svg
from openilink import Client as ILClient
from openilink import LoginCallbacks, MonitorOptions, extract_text
from openilink.types import MessageType

from ..dblog import log_event
from ..enums import ConnectorStatus
from .base import (
    RUNTIME_ERROR_KEY,
    RUNTIME_STATUS_KEY,
    InboundMessage,
    OutboundTask,
)

logger = logging.getLogger(__name__)


def qr_svg_data_url(content: str) -> str:
    image = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


class WeChatILinkConnector:
    platform = "wechat_ilink"

    def __init__(
        self,
        connector_id: int,
        config: dict[str, Any],
        on_message: Callable[[InboundMessage], Awaitable[None]],
        on_state: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.connector_id = connector_id
        self.config = dict(config)
        self._on_message = on_message
        self._on_state = on_state
        self._status = ConnectorStatus.OFFLINE
        self._login_state = "idle"
        self._qr_data_url = ""
        self._error = ""
        self._bot_id = str(config.get("bot_id", ""))
        self._last_sender = str(config.get("chat_id", ""))
        self._client: ILClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._monitor_thread: threading.Thread | None = None
        self._login_thread: threading.Thread | None = None

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if config:
            self.config.update(config)
        self._loop = asyncio.get_running_loop()
        if self.config.get("bot_token"):
            self._spawn_monitor()

    async def stop(self) -> None:
        if self._client:
            await asyncio.to_thread(self._client.stop)
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {
            "status": self._status.value,
            "platform": self.platform,
            "login_state": self._login_state,
            "bot_id": self._bot_id,
            "error": self._error,
            "has_token": bool(self.config.get("bot_token")),
        }

    async def login(self) -> dict[str, Any]:
        if self._login_thread and self._login_thread.is_alive():
            return self.login_snapshot()
        if self.config.get("bot_token"):
            self._login_state = "connected"
            return self.login_snapshot()
        self._login_state = "pending"
        self._error = ""
        self._login_thread = threading.Thread(
            target=self._run_login, daemon=True, name=f"ilink-login-{self.connector_id}"
        )
        self._login_thread.start()
        return self.login_snapshot()

    def _run_login(self) -> None:
        client = ILClient()

        def on_qrcode(content: str) -> None:
            self._qr_data_url = qr_svg_data_url(content)

        def on_scanned() -> None:
            self._login_state = "scanned"

        try:
            result = client.login_with_qr(
                LoginCallbacks(on_qrcode=on_qrcode, on_scanned=on_scanned), timeout=480
            )
        except Exception as exc:  # noqa: BLE001 - SDK login boundary
            self._login_state = "error"
            self._error = str(exc) or "登录失败"
            self._status = ConnectorStatus.ERROR
            log_event(
                "error",
                "connector.ilink",
                f"iLink 登录失败: {exc}",
                {"connector_id": self.connector_id},
            )
            self._persist()
            return
        if result.connected:
            self.config["bot_token"] = result.bot_token
            self.config["bot_id"] = result.bot_id
            self._bot_id = result.bot_id
            self._login_state = "connected"
            self._client = client
            self._status = ConnectorStatus.ONLINE
            self._persist()
            self._spawn_monitor()
        else:
            self._login_state = "error"
            self._error = result.message or "登录失败"

    def login_snapshot(self) -> dict[str, Any]:
        return {
            "state": self._login_state,
            "login_state": self._login_state,
            "qr": self._qr_data_url,
            "error": self._error,
            "bot_id": self._bot_id,
        }

    def _spawn_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._client = ILClient(token=str(self.config.get("bot_token", "")))
        self._status = ConnectorStatus.ONLINE
        self._monitor_thread = threading.Thread(
            target=self._run_monitor, daemon=True, name=f"ilink-{self.connector_id}"
        )
        self._monitor_thread.start()

    def _run_monitor(self) -> None:
        assert self._client and self._loop
        client = self._client

        def handler(msg: Any) -> None:
            text = extract_text(msg)
            if not text or msg.message_type is not MessageType.USER:
                return
            self._last_sender = str(msg.from_user_id)
            inbound = InboundMessage(
                connector_id=self.connector_id,
                sender_id=str(msg.from_user_id),
                text=text,
                conversation_id=str(msg.session_id),
                external_message_id=str(msg.message_id),
            )
            future = asyncio.run_coroutine_threadsafe(self._on_message(inbound), self._loop)
            future.add_done_callback(self._inbound_done)

        try:
            client.monitor(
                handler,
                opts=MonitorOptions(
                    initial_buf=str(self.config.get("sync_buf", "")),
                    on_buf_update=self._save_buf,
                    on_error=lambda e: self._note_error(str(e)),
                    on_session_expired=self._note_expired,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - SDK monitor boundary
            self._note_error(str(exc))

    def _save_buf(self, buf: str) -> None:
        self.config["sync_buf"] = buf
        self._persist()

    def _note_expired(self) -> None:
        self._status = ConnectorStatus.ERROR
        self._error = "iLink 会话已过期,请重新扫码登录"
        log_event(
            "warning", "connector.ilink", "iLink 会话已过期", {"connector_id": self.connector_id}
        )
        self._persist()

    def _note_error(self, message: str) -> None:
        self._status = ConnectorStatus.ERROR
        self._error = message
        log_event(
            "warning",
            "connector.ilink",
            f"iLink monitor 错误: {message}",
            {"connector_id": self.connector_id},
        )
        self._persist()

    def _persist(self) -> None:
        if self._on_state:
            try:
                self._on_state(
                    self.connector_id,
                    {
                        "bot_token": self.config.get("bot_token", ""),
                        "bot_id": self._config_bot_id(),
                        "sync_buf": self.config.get("sync_buf", ""),
                        "chat_id": self._last_sender,
                        RUNTIME_STATUS_KEY: self._status.value,
                        RUNTIME_ERROR_KEY: self._error,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - persistence callback boundary
                logger.warning("iLink 连接 %s 状态持久化失败: %s", self.connector_id, exc)

    def _inbound_done(self, future: Any) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("iLink 连接 %s 进站消息处理失败", self.connector_id)

    def _config_bot_id(self) -> str:
        return str(self.config.get("bot_id", ""))

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        client = self._client
        if client is None:
            raise RuntimeError("iLink 尚未登录")
        target = task.target or self._last_sender
        if not target:
            raise RuntimeError("iLink 发送需要目标用户(运营者先给 Bot 发一条消息激活)")
        body = await asyncio.to_thread(client.push, target, task.text)
        return {"accepted": True, "response": str(body)}

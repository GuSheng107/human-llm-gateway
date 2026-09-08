"""飞书（Lark）连接器：基于 lark-oapi SDK 的长连接（WebSocket）监听与消息收发。

SDK 的 ``lark.ws.Client.start()`` 是同步阻塞实现（使用模块级事件循环），
因此在独立线程中运行；进站事件通过 ``run_coroutine_threadsafe`` 桥接到网关
事件循环。出站消息经 ``lark.Client`` 的 im.v1 接口发送。

飞书使用 App ID + App Secret 凭据（无需扫码登录，``supports_login=False``），
通过长连接接收 ``im.message.receive_v1`` 事件。认证失败映射为 auth_required，
由连接管理器停止重试并等待所有者修复凭据。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
from typing import Any

from ...domain.connections import ERROR_AUTH, ERROR_DELIVERY, ERROR_NETWORK, ConnectorError
from ..base import Connector, ConnectorContext, DeliveryEnvelope, InboundMessage

logger = logging.getLogger(__name__)


def _classify(exc: Exception) -> ConnectorError:
    """把 SDK 异常归类为脱敏连接错误。"""
    text = type(exc).__name__
    # ClientException 在长连接握手/鉴权失败时抛出（如 App Secret 错误）。
    try:
        from lark_oapi.ws.exception import ClientException

        if isinstance(exc, ClientException):
            return ConnectorError(ERROR_AUTH, "飞书认证失败，请检查 App ID / App Secret")
    except Exception:  # 防御：SDK 内部类缺失时按网络错误兜底
        logger.debug("lark ClientException import failed", exc_info=True)
    return ConnectorError(ERROR_NETWORK, f"飞书连接错误: {text}")


class LarkConnector(Connector):
    platform = "lark"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self._client: Any | None = None  # lark.Client（出站 API）
        self._ws_client: Any | None = None  # lark.ws.Client（进站长连接）
        self._thread: threading.Thread | None = None
        self._thread_error: ConnectorError | None = None
        self._closed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbound = None  # InboundCallback
        # 会话定位：发送者 open_id -> chat_id（用于投递回同一会话）。
        self._chat_by_user: dict[str, str] = {}
        # 绑定成功时记录的目标会话，供 deliver 在缺少 reply_to_external_id 时回退。
        self._bound_user_id: str | None = None
        self._bound_chat_id: str | None = None

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not config.get("app_id"):
            problems.append("缺少 App ID")
        if not config.get("app_secret"):
            problems.append("缺少 App Secret")
        return problems

    def bind_inbound(self, callback) -> None:
        self._inbound = callback

    async def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._closed.clear()
        self._thread_error = None
        self._loop = asyncio.get_running_loop()
        config = self.ctx.config
        app_id = str(config.get("app_id") or "")
        app_secret = str(config.get("app_secret") or "")
        if not app_id or not app_secret:
            raise ConnectorError(ERROR_AUTH, "缺少飞书 App ID / App Secret")

        from lark_oapi import Client, EventDispatcherHandler
        from lark_oapi import ws as lark_ws

        # lark-oapi 的 ws.Client 使用模块级事件循环。若本方法在 uvicorn 的
        # 事件循环线程中被调用，模块级 loop 会绑定到正在运行的循环，导致
        # 后续子线程 run_until_complete 抛出 "This event loop is already
        # running"。这里强制把模块级 loop 替换为独立事件循环。
        import lark_oapi.ws.client as _ws_client_module

        if _ws_client_module.loop.is_running():
            _ws_client_module.loop = asyncio.new_event_loop()

        # 出站 API 客户端。
        self._client = Client.builder().app_id(app_id).app_secret(app_secret).build()
        # 进站事件分发器：注册消息接收回调。
        handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_event)
            .build()
        )
        # 长连接客户端；start() 阻塞，在独立线程运行。
        self._ws_client = lark_ws.Client(
            app_id=app_id, app_secret=app_secret, event_handler=handler
        )

        def _run() -> None:
            try:
                self._ws_client.start()
            except Exception as exc:  # noqa: BLE001  # 线程内兜底分类
                self._thread_error = _classify(exc)
            finally:
                self._closed.set()

        self._thread = threading.Thread(
            target=_run, name=f"lark-{self.ctx.connection_id}", daemon=True
        )
        self._thread.start()

    def _handle_event(self, event: Any) -> None:
        """SDK 线程事件回调：把飞书消息事件桥接为统一进站消息。"""
        if self._inbound is None or self._loop is None:
            return
        event_data = getattr(event, "event", None)
        if event_data is None:
            return
        sender = getattr(event_data, "sender", None)
        message = getattr(event_data, "message", None)
        if sender is None or message is None:
            return
        sender_id = getattr(sender, "sender_id", None)
        open_id = str(getattr(sender_id, "open_id", "") or "")
        user_id = str(getattr(sender_id, "user_id", "") or "")
        sender_external_id = open_id or user_id
        message_id = str(getattr(message, "message_id", "") or "")
        chat_id = str(getattr(message, "chat_id", "") or "")
        chat_type = str(getattr(message, "chat_type", "") or "").lower()
        message_type = str(getattr(message, "message_type", "") or "").lower()
        if not sender_external_id or not message_id:
            return

        text = self._extract_text(message, message_type)
        if not text:
            return
        # 记录发送者会话，用于投递回同一会话。
        if chat_id:
            self._chat_by_user[sender_external_id] = chat_id
        # 单聊（p2p）才允许绑定；群聊不设置绑定码。
        is_personal_chat = chat_type in {"p2p", ""}
        future = asyncio.run_coroutine_threadsafe(
            self._inbound(
                self.ctx.connection_id,
                InboundMessage(
                    external_message_id=message_id,
                    sender_external_id=sender_external_id,
                    text=text,
                    binding_code=text if is_personal_chat else None,
                    raw={"chat_id": chat_id, "chat_type": chat_type},
                ),
            ),
            self._loop,
        )
        try:
            result = future.result(timeout=10)
            result_value = getattr(result, "value", result)
            if result_value == "bound":
                self._bound_user_id = sender_external_id
                self._bound_chat_id = chat_id or sender_external_id
                if self._client is not None:
                    self._send_text(
                        self._bound_chat_id,
                        "连接绑定成功，可以开始接收任务。",
                        "chat_id" if chat_id else "open_id",
                    )
            elif text == "connect lark" and not is_personal_chat and self._client is not None:
                self._send_text(
                    chat_id,
                    "绑定失败，请在与机器人的单聊会话中发送 connect lark。",
                    "chat_id",
                )
        except Exception:  # 不让进站异常终止监听线程
            logger.exception("lark inbound handling failed")

    @staticmethod
    def _extract_text(message: Any, message_type: str) -> str:
        """从飞书消息事件中提取文本内容（仅处理文本消息）。"""
        if message_type != "text":
            return ""
        content = str(getattr(message, "content", "") or "")
        if not content:
            return ""
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return ""
        return str(data.get("text") or "").strip()

    def _send_text(self, target: str, text: str, receive_id_type: str) -> None:
        """同步发送文本消息（在 SDK 线程或 to_thread 中调用）。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(target)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            CreateMessageRequest.builder().receive_id_type(receive_id_type).request_body(body).build()
        )
        response = self._client.im.v1.message.create(request)
        if not getattr(response, "success", lambda: True)():
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "")
            raise ConnectorError(ERROR_DELIVERY, f"飞书消息发送失败: {code} {msg}".strip())

    async def wait_closed(self) -> None:
        if self._thread is None:
            return
        await asyncio.to_thread(self._closed.wait)

    def last_error(self) -> ConnectorError | None:
        return self._thread_error

    async def stop(self) -> None:
        # lark.ws.Client 无公开 stop()；通过模块级事件循环调用私有 _disconnect。
        ws_client = self._ws_client
        if ws_client is not None:
            try:
                import lark_oapi.ws.client as ws_client_module

                loop = ws_client_module.loop
                disconnect = getattr(ws_client, "_disconnect", None)
                if disconnect is not None and loop is not None:
                    future = asyncio.run_coroutine_threadsafe(disconnect(), loop)
                    try:
                        future.result(timeout=5)
                    except Exception:  # 关闭失败不阻塞停止流程
                        logger.info("lark ws disconnect failed", exc_info=True)
            except Exception:
                logger.info("lark ws stop failed", exc_info=True)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, timeout=10)
            self._thread = None
        self._ws_client = None
        self._client = None
        self._closed.set()

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        client = self._client
        if client is None or self._thread is None or not self._thread.is_alive():
            raise ConnectorError(ERROR_DELIVERY, "飞书连接不在线")
        # 目标定位：显式 reply_to_external_id 优先，否则回退到绑定会话。
        target = envelope.reply_to_external_id or self._bound_chat_id or self._bound_user_id or ""
        if not target:
            raise ConnectorError(ERROR_DELIVERY, "缺少投递目标")
        receive_id_type = "chat_id" if target == self._bound_chat_id else "open_id"
        try:
            for message in envelope.effective_messages():
                await asyncio.to_thread(self._send_text, target, message, receive_id_type)
        except ConnectorError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def send_reply_text(
        self, external_user_id: str, text: str, *, context_token: str | None = None
    ) -> None:
        """主动发送文本（/page 外发通路）。"""
        client = self._client
        if client is None or self._thread is None or not self._thread.is_alive():
            raise ConnectorError(ERROR_DELIVERY, "飞书连接不在线")
        if not external_user_id:
            raise ConnectorError(ERROR_DELIVERY, "缺少发送目标")
        try:
            await asyncio.to_thread(self._send_text, external_user_id, text, "open_id")
        except ConnectorError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    async def send_file(self, external_user_id: str, filename: str, content: str) -> None:
        """主动发送文件（/file 外发通路）：上传文件后发送 file 消息。"""
        client = self._client
        if client is None or self._thread is None or not self._thread.is_alive():
            raise ConnectorError(ERROR_DELIVERY, "飞书连接不在线")
        if not external_user_id:
            raise ConnectorError(ERROR_DELIVERY, "缺少发送目标")
        try:
            file_key = await asyncio.to_thread(self._upload_file, filename, content)
            if not file_key:
                raise ConnectorError(ERROR_DELIVERY, "飞书文件上传未返回 file_key")
            await asyncio.to_thread(
                self._send_file_message, external_user_id, file_key, filename
            )
        except ConnectorError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc

    def _upload_file(self, filename: str, content: str) -> str:
        """上传文件到飞书，返回 file_key。"""
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

        file_bytes = content.encode("utf-8")
        body = (
            CreateFileRequestBody.builder()
            .file_type("stream")
            .file_name(filename)
            .file(io.BytesIO(file_bytes))
            .build()
        )
        request = CreateFileRequest.builder().request_body(body).build()
        response = self._client.im.v1.file.create(request)
        if not getattr(response, "success", lambda: True)():
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "")
            raise ConnectorError(ERROR_DELIVERY, f"飞书文件上传失败: {code} {msg}".strip())
        data = getattr(response, "data", None)
        return str(getattr(data, "file_key", "") or "")

    def _send_file_message(self, target: str, file_key: str, filename: str) -> None:
        """发送文件消息。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(target)
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
            .build()
        )
        request = (
            CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
        )
        response = self._client.im.v1.message.create(request)
        if not getattr(response, "success", lambda: True)():
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "")
            raise ConnectorError(ERROR_DELIVERY, f"飞书文件消息发送失败: {code} {msg}".strip())

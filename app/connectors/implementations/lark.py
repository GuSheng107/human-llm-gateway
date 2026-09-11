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
import concurrent.futures
import io
import json
import logging
import threading
from typing import Any

from ...domain.connections import (
    ERROR_AUTH,
    ERROR_CONFIG,
    ERROR_DELIVERY,
    ERROR_NETWORK,
    ConnectorError,
)
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

    # lark-oapi 的 ws.Client 硬编码使用模块级单例事件循环
    # （lark_oapi.ws.client.loop），同一进程内无法安全支持多个长连接实例共享
    # 该 loop。用类级锁保证同一时刻至多一个实例独占模块级 loop：第二个实例
    # 启动时明确报错（config_invalid），而非静默替换 loop 导致连接混乱。
    _module_loop_lock = threading.Lock()
    _active_instance: LarkConnector | None = None

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
        # 该状态在 SDK 线程写入、在事件循环线程（deliver）读取，用锁保护以保证
        # 三个字段作为一致的整体快照被读取，避免读到部分更新的组合。
        self._bound_lock = threading.Lock()
        self._bound_user_id: str | None = None
        self._bound_chat_id: str | None = None
        # _bound_chat_id 的目标类型（"chat_id" 或 "open_id"）：chat_id 缺失时
        # _bound_chat_id 回退为发送者 open_id，deliver 据此选择正确的 receive_id_type。
        self._bound_chat_type: str = "open_id"
        # 进程重启后内存绑定态为空：用数据库持久化的绑定用户预填投递目标，
        # 避免任务提醒推送因"缺少投递目标"失败（chat_id 不可知时按 open_id 发送）。
        if self.ctx.bound_external_user_id:
            self._bound_user_id = self.ctx.bound_external_user_id

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

        # 独占模块级 loop：同一进程内仅允许一个飞书长连接实例。lark-oapi 的
        # ws.Client 在内部直接引用模块级 loop（lark_oapi.ws.client.loop），
        # 若多个实例各自替换该 loop，会让已运行实例的后续任务被调度到被替换
        # 后的新 loop 上，导致连接错乱。这里用类级锁保证互斥，并在已有活跃
        # 实例时明确报错。
        with LarkConnector._module_loop_lock:
            active = LarkConnector._active_instance
            if active is not None and active is not self:
                raise ConnectorError(ERROR_CONFIG, "同一进程内仅支持一个飞书长连接实例")
            LarkConnector._active_instance = self
            try:
                # lark-oapi 的 ws.Client 使用模块级事件循环。若本方法在 uvicorn 的
                # 事件循环线程中被调用，模块级 loop 会绑定到正在运行的循环，导致
                # 后续子线程 run_until_complete 抛出 "This event loop is already
                # running"。这里强制把模块级 loop 替换为独立事件循环，并确保
                # _run 线程内的 asyncio 默认循环与模块级 loop 一致。
                # 若上一实例的线程已退出但 loop 处于 stopped 状态（不可复用），
                # 也一并替换为新 loop，避免复用已停止的循环。
                import lark_oapi.ws.client as _ws_client_module

                if _ws_client_module.loop.is_running() or not _ws_client_module.loop.is_closed():
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
                from lark_oapi.core.enum import LogLevel as _LarkLogLevel

                self._ws_client = lark_ws.Client(
                    app_id=app_id,
                    app_secret=app_secret,
                    event_handler=handler,
                    log_level=_LarkLogLevel.DEBUG,
                )

                def _run() -> None:
                    try:
                        # 在子线程内确保 asyncio 默认事件循环与模块级 loop 一致，
                        # 避免 lark-oapi 内部通过 asyncio.get_event_loop() 取到与
                        # 模块级 loop 不同的循环导致 run_until_complete 冲突。
                        import lark_oapi.ws.client as _ws_client_module

                        asyncio.set_event_loop(_ws_client_module.loop)
                        self._ws_client.start()
                    except Exception as exc:  # noqa: BLE001  # 线程内兜底分类
                        self._thread_error = _classify(exc)
                    finally:
                        self._closed.set()

                self._thread = threading.Thread(
                    target=_run, name=f"lark-{self.ctx.connection_id}", daemon=True
                )
                self._thread.start()
            except Exception:
                # 初始化失败时释放独占权，避免 _active_instance 残留导致后续
                # 无法启动新实例。
                if LarkConnector._active_instance is self:
                    LarkConnector._active_instance = None
                raise

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
        # 通过 run_coroutine_threadsafe 桥接到网关事件循环，并在回调中处理
        # 结果，避免阻塞 SDK 线程（future.result 会阻塞至多 10s，期间 SDK
        # 无法处理后续事件；超时还会丢弃该消息）。
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
        future.add_done_callback(
            lambda f: self._handle_inbound_result(
                f,
                sender_external_id=sender_external_id,
                chat_id=chat_id,
                text=text,
                is_personal_chat=is_personal_chat,
            )
        )

    def _handle_inbound_result(
        self,
        future: concurrent.futures.Future[Any],
        *,
        sender_external_id: str,
        chat_id: str,
        text: str,
        is_personal_chat: bool,
    ) -> None:
        """进站回调完成后的处理（在事件循环线程执行）。"""
        try:
            result = future.result()
        except Exception:  # 不让进站异常影响 SDK 线程
            logger.exception("lark inbound handling failed")
            return
        result_value = getattr(result, "value", result)
        if result_value == "bound":
            with self._bound_lock:
                self._bound_user_id = sender_external_id
                self._bound_chat_id = chat_id or sender_external_id
                self._bound_chat_type = "chat_id" if chat_id else "open_id"
            if self._client is not None:
                # 在事件循环线程内调度同步阻塞的 SDK 发送，避免阻塞事件循环。
                self._schedule_send_text(
                    self._bound_chat_id,
                    "连接绑定成功，可以开始接收任务。",
                    self._bound_chat_type,
                )
        elif text == "connect lark" and not is_personal_chat and self._client is not None:
            self._schedule_send_text(
                chat_id,
                "绑定失败，请在与机器人的单聊会话中发送 connect lark。",
                "chat_id",
            )

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

    def _schedule_send_text(self, target: str, text: str, receive_id_type: str) -> None:
        """在事件循环线程内调度同步阻塞的 SDK 发送，避免阻塞事件循环。

        `_handle_inbound_result` 运行在事件循环线程（add_done_callback），
        直接调用同步阻塞的 `_send_text` 会卡住整个事件循环，故用
        `asyncio.to_thread` 把阻塞调用挪到线程池。发送失败仅记录，不向上抛，
        因为此处是绑定反馈提示，非关键路径。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 不在事件循环线程（异常情况）：退化为线程池直接执行。
            asyncio.run(asyncio.to_thread(self._send_text, target, text, receive_id_type))
            return
        future = asyncio.run_coroutine_threadsafe(
            asyncio.to_thread(self._send_text, target, text, receive_id_type), loop
        )

        def _log_failure(f: concurrent.futures.Future[Any]) -> None:
            try:
                f.result()
            except Exception:
                logger.exception("lark bound feedback send failed")

        future.add_done_callback(_log_failure)

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
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
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
        thread = self._thread
        if thread is not None:
            # join 超时后线程可能仍存活并继续引用模块级 loop。此时不能直接
            # 释放独占权，否则新实例替换 loop 会让旧线程的任务被调度到新 loop。
            await asyncio.to_thread(thread.join, timeout=10)
            if thread.is_alive():
                logger.warning(
                    "lark ws thread did not exit within timeout; "
                    "refusing to release module-level loop ownership"
                )
                # 线程未退出时保留 _active_instance 独占权，避免新实例与旧线程
                # 共享/替换模块级 loop 导致连接错乱。同时保留 _thread 引用，
                # 便于后续 stop 重试。抛错告知调用方关闭未完全完成。
                raise ConnectorError(ERROR_NETWORK, "飞书连接线程未能及时退出")
            self._thread = None
        self._ws_client = None
        self._client = None
        self._closed.set()
        # 释放模块级 loop 独占权，允许后续重新启动或其他实例接管。
        with LarkConnector._module_loop_lock:
            if LarkConnector._active_instance is self:
                LarkConnector._active_instance = None

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        client = self._client
        if client is None or self._thread is None or not self._thread.is_alive():
            raise ConnectorError(ERROR_DELIVERY, "飞书连接不在线")
        # 目标定位与类型：显式 reply_to_external_id（飞书语义为 chat_id）优先，
        # 否则回退到绑定会话。回退到 _bound_chat_id 时按其记录的类型发送
        # （chat_id 缺失时 _bound_chat_id 存的是发送者 open_id，此时为 open_id）；
        # 回退到 _bound_user_id 时按 open_id 发送。绑定状态在 SDK 线程写入，
        # 这里在锁内读取一致快照，避免读到部分更新的组合。
        if envelope.reply_to_external_id:
            target = envelope.reply_to_external_id
            receive_id_type = "chat_id"
        else:
            with self._bound_lock:
                bound_chat_id = self._bound_chat_id
                bound_chat_type = self._bound_chat_type
                bound_user_id = self._bound_user_id
            if bound_chat_id:
                target = bound_chat_id
                receive_id_type = bound_chat_type
            elif bound_user_id:
                target = bound_user_id
                receive_id_type = "open_id"
            else:
                raise ConnectorError(ERROR_DELIVERY, "缺少投递目标")
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
                self._send_file_message, external_user_id, file_key, filename, "open_id"
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

    def _send_file_message(
        self, target: str, file_key: str, filename: str, receive_id_type: str
    ) -> None:
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
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not getattr(response, "success", lambda: True)():
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "")
            raise ConnectorError(ERROR_DELIVERY, f"飞书文件消息发送失败: {code} {msg}".strip())

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.connectors.base import ConnectorContext, DeliveryEnvelope
from app.connectors.implementations.lark import LarkConnector


class _FakeMessage:
    def __init__(self, message_id: str, chat_id: str, chat_type: str, content: str) -> None:
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat_type = chat_type
        self.message_type = "text"
        self.content = content


class _FakeSenderId:
    def __init__(self, open_id: str) -> None:
        self.open_id = open_id
        self.user_id = ""


class _FakeSender:
    def __init__(self, open_id: str) -> None:
        self.sender_id = _FakeSenderId(open_id)
        self.sender_type = "user"


class _FakeEventData:
    def __init__(self, open_id: str, message: _FakeMessage) -> None:
        self.sender = _FakeSender(open_id)
        self.message = message


class _FakeEvent:
    def __init__(self, open_id: str, message: _FakeMessage) -> None:
        self.event = _FakeEventData(open_id, message)


class _FakeResponse:
    def __init__(self, success: bool = True) -> None:
        self.code = 0 if success else 999
        self.msg = "" if success else "error"
        self.data = None

    def success(self) -> bool:
        return self.code == 0


class _FakeMessageService:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    def create(self, request) -> _FakeResponse:
        body = getattr(request, "request_body", None)
        receive_id_type = getattr(request, "receive_id_type", "")
        self.sent.append((receive_id_type, body))
        return _FakeResponse()


class _FakeFileService:
    def __init__(self) -> None:
        self.uploaded: list[Any] = []

    def create(self, request) -> _FakeResponse:
        body = getattr(request, "request_body", None)
        self.uploaded.append(body)
        resp = _FakeResponse()
        resp.data = type("D", (), {"file_key": "file-key-1"})()
        return resp


class _FakeImV1:
    def __init__(self) -> None:
        self.message = _FakeMessageService()
        self.file = _FakeFileService()


class _FakeIm:
    def __init__(self) -> None:
        self.v1 = _FakeImV1()


class _FakeClient:
    def __init__(self) -> None:
        self.im = _FakeIm()


def _connector() -> LarkConnector:
    return LarkConnector(
        ConnectorContext(
            connection_id=7,
            owner_user_id=9,
            name="lark",
            platform="lark",
            config={"app_id": "app", "app_secret": "secret"},
        )
    )


class _LoopThread:
    """在后台线程运行事件循环，供 run_coroutine_threadsafe 调度。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


def test_validate_config_requires_app_credentials() -> None:
    assert LarkConnector.validate_config({"app_id": "app", "app_secret": "secret"}) == []
    assert "App ID" in " ".join(LarkConnector.validate_config({"app_secret": "secret"}))
    assert "App Secret" in " ".join(LarkConnector.validate_config({"app_id": "app"}))


def test_p2p_text_message_sets_binding_code() -> None:
    connector = _connector()
    loop_thread = _LoopThread()
    connector._loop = loop_thread.loop
    captured = []

    async def inbound(connection_id, message):
        captured.append((connection_id, message))
        return "unbound"

    connector.bind_inbound(inbound)
    try:
        event = _FakeEvent(
            "ou_open-1",
            _FakeMessage("msg-1", "oc_chat-1", "p2p", '{"text":"connect lark"}'),
        )
        connector._handle_event(event)
    finally:
        loop_thread.stop()

    assert captured[0][0] == 7
    message = captured[0][1]
    assert message.sender_external_id == "ou_open-1"
    assert message.external_message_id == "msg-1"
    assert message.text == "connect lark"
    assert message.binding_code == "connect lark"
    assert message.raw["chat_id"] == "oc_chat-1"


def test_group_chat_has_no_binding_code() -> None:
    connector = _connector()
    loop_thread = _LoopThread()
    connector._loop = loop_thread.loop
    captured = []

    async def inbound(connection_id, message):
        captured.append((connection_id, message))
        return "unbound"

    connector.bind_inbound(inbound)
    try:
        event = _FakeEvent(
            "ou_open-1",
            _FakeMessage("msg-1", "oc_group-1", "group", '{"text":"connect lark"}'),
        )
        connector._handle_event(event)
    finally:
        loop_thread.stop()

    assert captured[0][1].binding_code is None


def test_non_text_message_is_ignored() -> None:
    connector = _connector()
    loop_thread = _LoopThread()
    connector._loop = loop_thread.loop
    captured = []

    async def inbound(connection_id, message):
        captured.append(message)
        return "unbound"

    connector.bind_inbound(inbound)
    try:
        event = _FakeEvent(
            "ou_open-1",
            _FakeMessage("msg-1", "oc_chat-1", "p2p", '{"image_key":"img-1"}'),
        )
        event.event.message.message_type = "image"
        connector._handle_event(event)
    finally:
        loop_thread.stop()

    assert captured == []


def test_delivery_uses_sdk_message_contract() -> None:
    connector = _connector()
    client = _FakeClient()
    connector._client = client
    connector._thread = type("T", (), {"is_alive": lambda self: True})()
    connector._bound_chat_id = "oc_chat-1"

    envelope = DeliveryEnvelope(
        task_public_id="task-1",
        requested_model="fake-model",
        prompt_text="请处理任务",
        owner_user_id=9,
        reply_to_external_id="oc_chat-1",
    )
    asyncio.run(connector.deliver(envelope))

    assert len(client.im.v1.message.sent) == 1
    receive_id_type, body = client.im.v1.message.sent[0]
    assert receive_id_type == "chat_id"
    assert body.receive_id == "oc_chat-1"
    assert body.msg_type == "text"
    assert body.content == '{"text": "请处理任务"}'


def test_send_reply_text_uses_open_id() -> None:
    connector = _connector()
    client = _FakeClient()
    connector._client = client
    connector._thread = type("T", (), {"is_alive": lambda self: True})()

    asyncio.run(connector.send_reply_text("ou_open-1", "你好"))

    receive_id_type, body = client.im.v1.message.sent[0]
    assert receive_id_type == "open_id"
    assert body.receive_id == "ou_open-1"
    assert body.msg_type == "text"

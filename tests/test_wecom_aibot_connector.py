from __future__ import annotations

import asyncio

from app.connectors.base import ConnectorContext, DeliveryEnvelope
from app.connectors.implementations.wecom_aibot import WeComAibotConnector


class _FakeClient:
    def __init__(self) -> None:
        self.replies: list[tuple[dict, dict]] = []
        self.sent: list[tuple[str, dict]] = []

    async def reply(self, frame: dict, body: dict) -> None:
        self.replies.append((frame, body))

    async def send_message(self, chat_id: str, body: dict) -> None:
        self.sent.append((chat_id, body))

    def is_connected(self) -> bool:
        return True


def _connector() -> WeComAibotConnector:
    return WeComAibotConnector(
        ConnectorContext(
            connection_id=7,
            owner_user_id=9,
            name="mycom",
            platform="wecom_aibot",
            config={"bot_id": "bot", "secret": "secret"},
        )
    )


def test_text_message_uses_generic_connect_command_for_binding() -> None:
    connector = _connector()
    client = _FakeClient()
    connector._client = client
    captured = []

    async def inbound(connection_id, message):
        captured.append((connection_id, message))
        return "bound"

    connector.bind_inbound(inbound)
    frame = {
        "headers": {"req_id": "req-1"},
        "body": {
            "msgid": "msg-1",
            "from": {"userid": "user-1"},
            "text": {"content": "connect mycom"},
            "chatid": "user-1",
            "chattype": "single",
        },
    }
    asyncio.run(connector._handle_text_message(frame))

    assert captured[0][0] == 7
    message = captured[0][1]
    assert message.sender_external_id == "user-1"
    assert message.binding_code == "connect mycom"
    assert client.replies[0][1]["text"]["content"].startswith("连接绑定成功")


def test_group_chat_cannot_bind_wecom_connection() -> None:
    connector = _connector()
    client = _FakeClient()
    connector._client = client
    captured = []

    async def inbound(connection_id, message):
        captured.append((connection_id, message))
        return "unbound"

    connector.bind_inbound(inbound)
    frame = {
        "headers": {"req_id": "req-group"},
        "body": {
            "msgid": "msg-group",
            "from": {"userid": "user-1"},
            "text": {"content": "connect mycom"},
            "chatid": "room-1",
            "chattype": "group",
        },
    }
    asyncio.run(connector._handle_text_message(frame))

    assert captured[0][1].binding_code is None
    assert "个人会话" in client.replies[0][1]["text"]["content"]


def test_delivery_uses_sdk_message_body_contract() -> None:
    connector = _connector()
    client = _FakeClient()
    connector._client = client

    envelope = DeliveryEnvelope(
        task_public_id="task-1",
        requested_model="fake-model",
        prompt_text="请处理任务",
        owner_user_id=9,
        reply_to_external_id="chat-1",
    )
    asyncio.run(connector.deliver(envelope))
    assert client.sent == [
        ("chat-1", {"msgtype": "markdown", "markdown": {"content": "请处理任务"}})
    ]

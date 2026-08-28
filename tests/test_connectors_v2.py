import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

import app.db as database
from app.connection_config import dump_connection_config, load_connection_config
from app.connectors.base import RUNTIME_ERROR_KEY, RUNTIME_STATUS_KEY
from app.connectors.http_poll import HttpPollConnector
from app.connectors.ilink import WeChatILinkConnector, qr_svg_data_url
from app.connectors.manager import ConnectorManager
from app.connectors.webhook import WebhookConnector
from app.connectors.websocket import WebSocketConnector
from app.connectors.wecom import WeComConnector, extract_wecom_message
from app.enums import ConnectorPlatform, TaskStatus
from app.models import IMConnection, RequestTask


def create_bot(client, headers, *, platform="webhook", name="My Bot", config=None):
    response = client.post(
        "/api/im-connections",
        headers=headers,
        json={"name": name, "platform": platform, "config": config or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ensure_catalog(client, admin_headers, model_id):
    listed = client.get("/api/model-catalog", headers=admin_headers).json()
    for item in listed:
        if item["model_id"] == model_id:
            return item
    response = client.post("/api/model-catalog", headers=admin_headers, json={"model_id": model_id})
    assert response.status_code == 200, response.text
    return response.json()


def create_key(client, user_headers, admin_headers, connection_id, *, name="bot-key"):
    ensure_catalog(client, admin_headers, "human-default")
    route = client.post(
        "/api/model-routes",
        headers=user_headers,
        json={"name": f"{name}-route", "model_name": "human-default", "mode": "human"},
    )
    assert route.status_code == 200, route.text
    response = client.post(
        "/api/api-keys",
        headers=user_headers,
        json={"name": name, "route_id": route.json()["id"], "im_connection_id": connection_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def bind_webhook(client, user_headers, bot):
    binding = client.post(
        f"/api/im-connections/{bot['id']}/binding",
        headers=user_headers,
    )
    assert binding.status_code == 200, binding.text
    command = binding.json()["command"]
    missing_sender = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers={"X-Connector-Token": bot["setup"]["inbound_token"]},
        json={"external_message_id": "bind-missing-sender", "text": command},
    )
    assert missing_sender.status_code == 200
    assert (
        client.get("/api/im-connections", headers=user_headers).json()[0]["binding_status"]
        == "waiting"
    )
    inbound = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers={"X-Connector-Token": bot["setup"]["inbound_token"]},
        json={
            "sender_id": "operator-im-user",
            "conversation_id": "operator-chat",
            "external_message_id": "bind-1",
            "text": command,
        },
    )
    assert inbound.status_code == 200, inbound.text


def add_waiting_task(api_key_id):
    with database.SessionLocal() as db:
        task = RequestTask(
            api_key_id=api_key_id,
            protocol="openai",
            model="human-default",
            request_json="{}",
            status=TaskStatus.HUMAN_WAITING,
        )
        db.add(task)
        db.commit()
        return task.id


def task_status(task_id):
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        assert task is not None
        return task.status


def test_qr_svg_data_url_is_renderable():
    url = qr_svg_data_url("https://login.example/xyz")
    assert url.startswith("data:image/svg+xml;base64,")
    assert len(url) > 200


def test_ilink_login_snapshot_supports_frontend_contract():
    async def ignore_message(_message):
        return None

    connector = WeChatILinkConnector(1, {}, ignore_message)
    snapshot = connector.login_snapshot()
    assert snapshot["state"] == "idle"
    assert snapshot["login_state"] == "idle"


def test_ilink_login_failure_updates_state(monkeypatch):
    async def ignore_message(_message):
        return None

    connector = WeChatILinkConnector(1, {}, ignore_message)
    monkeypatch.setattr(
        "app.connectors.ilink.ILClient.login_with_qr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    connector._run_login()
    snapshot = connector.login_snapshot()
    assert snapshot["login_state"] == "error"
    assert snapshot["error"] == "provider down"


def test_wecom_sdk_long_connection_and_message_normalization(monkeypatch):
    received = []
    states = []

    async def on_message(message):
        received.append(message)

    class StubWSClient:
        instance = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.handlers = {}
            self.sent = []
            StubWSClient.instance = self

        def on(self, event, handler):
            self.handlers[event] = handler
            return self

        async def connect(self):
            return self

        async def disconnect(self):
            return None

        async def send_message(self, chatid, body):
            self.sent.append((chatid, body))
            return {"ok": True}

    monkeypatch.setattr("app.connectors.wecom.WSClient", StubWSClient)

    async def run():
        connector = WeComConnector(
            7,
            {"bot_id": "bot-id", "secret": "bot-secret"},
            on_message,
            lambda connector_id, state: states.append((connector_id, state)),
        )
        await connector.start()
        sdk = StubWSClient.instance
        assert sdk is not None
        assert sdk.kwargs["bot_id"] == "bot-id"
        await sdk.handlers["authenticated"]()
        await sdk.handlers["message"](
            {
                "headers": {"req_id": "request-1"},
                "body": {
                    "from": {"userid": "wecom-user"},
                    "chatid": "wecom-chat",
                    "text": {"content": "/reply\n完成\n/done"},
                },
            }
        )
        assert (await connector.health())["authenticated"] is True
        assert states[-1][1][RUNTIME_STATUS_KEY] == "online"
        assert received[0].sender_id == "wecom-user"
        assert received[0].conversation_id == "wecom-chat"
        assert received[0].external_message_id == "request-1"
        await connector.send_task(
            type("Task", (), {"task_id": "t", "text": "hello", "target": "wecom-chat"})()
        )
        assert sdk.sent == [
            (
                "wecom-chat",
                {"msgtype": "markdown", "markdown": {"content": "hello"}},
            )
        ]
        await connector.stop()

    asyncio.run(run())


def test_wecom_message_without_sender_is_ignored():
    assert extract_wecom_message(1, {"body": {"text": {"content": "hello"}}}) is None


def test_user_owns_bot_admin_only_manages_it(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers)
    assert bot["owner_name"] == "测试操作员"
    assert bot["setup"]["inbound_token"]

    own = client.get("/api/im-connections", headers=user_headers)
    all_connections = client.get("/api/im-connections", headers=admin_headers)
    assert [item["id"] for item in own.json()] == [bot["id"]]
    assert [item["id"] for item in all_connections.json()] == [bot["id"]]

    denied_create = client.post(
        "/api/im-connections",
        headers=admin_headers,
        json={"name": "Admin Bot", "platform": "webhook", "config": {}},
    )
    assert denied_create.status_code == 403

    start = client.post(f"/api/im-connections/{bot['id']}/start", headers=admin_headers)
    assert start.status_code == 200
    assert start.json()["platform"] == "webhook"
    stop = client.post(f"/api/im-connections/{bot['id']}/stop", headers=admin_headers)
    assert stop.json() == {"stopped": True}
    deleted = client.post(f"/api/im-connections/{bot['id']}/delete", headers=admin_headers)
    assert deleted.json() == {"deleted": True}
    assert client.get("/api/im-connections", headers=user_headers).json() == []


def test_connector_config_is_encrypted_without_legacy_fallback(client, user_headers):
    bot = create_bot(client, user_headers, config={"inbound_token": "secret-token"})
    with database.SessionLocal() as db:
        connection = db.get(IMConnection, bot["id"])
        assert connection is not None
        assert connection.config_json.startswith("enc:v1:")
        assert "secret-token" not in connection.config_json
        assert load_connection_config(connection.config_json)["inbound_token"] == "secret-token"
    with pytest.raises(ValueError):
        load_connection_config('{"inbound_token":"old-row"}')


def test_runtime_state_updates_status_without_polluting_config(client, user_headers):
    bot = create_bot(client, user_headers)
    client.app.state.connector_manager._persist_state(
        bot["id"],
        {RUNTIME_STATUS_KEY: "error", RUNTIME_ERROR_KEY: "connection lost"},
    )
    listed = client.get("/api/im-connections", headers=user_headers).json()[0]
    assert listed["status"] == "error"
    assert listed["last_error"] == "connection lost"
    with database.SessionLocal() as db:
        connection = db.get(IMConnection, bot["id"])
        assert connection is not None
        config = load_connection_config(connection.config_json)
        assert RUNTIME_STATUS_KEY not in config
        assert RUNTIME_ERROR_KEY not in config


def test_webhook_binding_and_exact_task_reply(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers)
    bind_webhook(client, user_headers, bot)
    listed = client.get("/api/im-connections", headers=user_headers).json()[0]
    assert listed["binding_status"] == "bound"
    assert listed["bound_user_id"] == "operator-im-user"

    key = create_key(client, user_headers, admin_headers, bot["id"])
    task_id = add_waiting_task(key["id"])
    rejected = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers={"X-Connector-Token": bot["setup"]["inbound_token"]},
        json={
            "sender_id": "someone-else",
            "external_message_id": "wrong-sender",
            "reply_to_task_id": task_id,
            "text": "/reply\nwrong\n/done",
        },
    )
    assert rejected.status_code == 200
    assert task_status(task_id) is TaskStatus.HUMAN_WAITING

    accepted = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers={"X-Connector-Token": bot["setup"]["inbound_token"]},
        json={
            "sender_id": "operator-im-user",
            "conversation_id": "operator-chat",
            "external_message_id": "reply-1",
            "reply_to_task_id": task_id,
            "text": '/think\ncheck\n/tool lookup {"id":1}\n/reply\nok\n/done',
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert task_status(task_id) is TaskStatus.PSEUDO_STREAMING

    bad_token = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers={"X-Connector-Token": "wrong"},
        json={"sender_id": "operator-im-user", "text": "x"},
    )
    assert bad_token.status_code == 403

    deleted = client.post(f"/api/im-connections/{bot['id']}/delete", headers=user_headers)
    assert deleted.status_code == 200
    assert (
        client.get("/v1/models", headers={"Authorization": f"Bearer {key['secret']}"}).status_code
        == 401
    )
    with database.SessionLocal() as db:
        connection = db.get(IMConnection, bot["id"])
        assert connection is not None
        assert load_connection_config(connection.config_json) == {}
        assert connection.bound_user_id == ""


def test_multiple_waiting_tasks_require_id_and_receipts_are_global(
    client, admin_headers, user_headers
):
    bot = create_bot(client, user_headers)
    bind_webhook(client, user_headers, bot)
    key = create_key(client, user_headers, admin_headers, bot["id"])
    first = add_waiting_task(key["id"])
    second = add_waiting_task(key["id"])
    token_headers = {"X-Connector-Token": bot["setup"]["inbound_token"]}

    ambiguous = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers=token_headers,
        json={
            "sender_id": "operator-im-user",
            "external_message_id": "ambiguous-1",
            "text": "/reply\nwhich one\n/done",
        },
    )
    assert ambiguous.status_code == 200
    assert task_status(first) is TaskStatus.HUMAN_WAITING
    assert task_status(second) is TaskStatus.HUMAN_WAITING

    explicit = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers=token_headers,
        json={
            "sender_id": "operator-im-user",
            "external_message_id": "global-message-id",
            "reply_to_task_id": first,
            "text": "/reply\nfirst\n/done",
        },
    )
    assert explicit.status_code == 200
    assert task_status(first) is TaskStatus.PSEUDO_STREAMING

    duplicate = client.post(
        f"/connectors/webhook/{bot['id']}/inbound",
        headers=token_headers,
        json={
            "sender_id": "operator-im-user",
            "external_message_id": "global-message-id",
            "reply_to_task_id": second,
            "text": "/reply\nsecond\n/done",
        },
    )
    assert duplicate.status_code == 200
    assert task_status(second) is TaskStatus.HUMAN_WAITING


def test_websocket_user_binding_reply_and_auth(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers, platform="websocket", name="My WS")
    binding = client.post(f"/api/im-connections/{bot['id']}/binding", headers=user_headers).json()
    ws_url = f"/connectors/ws/{bot['id']}?token={bot['setup']['auth_token']}"
    with client.websocket_connect(ws_url) as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "sender_id": "ws-user",
                    "conversation_id": "ws-chat",
                    "external_message_id": "ws-bind",
                    "text": binding["command"],
                }
            )
        )

    key = create_key(client, user_headers, admin_headers, bot["id"], name="ws-key")
    task_id = add_waiting_task(key["id"])
    with client.websocket_connect(ws_url) as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "sender_id": "ws-user",
                    "external_message_id": "ws-reply",
                    "reply_to_task_id": task_id,
                    "text": "/reply\nwebsocket answer\n/done",
                }
            )
        )
    assert task_status(task_id) is TaskStatus.PSEUDO_STREAMING

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/connectors/ws/{bot['id']}?token=wrong"),
    ):
        pass
    assert exc_info.value.code == 4401


def test_login_actions_are_owner_only_and_platform_specific(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers)
    ordinary = client.post(f"/api/im-connections/{bot['id']}/login", headers=user_headers)
    assert ordinary.status_code == 400
    admin = client.post(f"/api/im-connections/{bot['id']}/login", headers=admin_headers)
    assert admin.status_code == 403


def test_old_connector_and_login_routes_do_not_exist(client, admin_headers):
    # 旧后端路径 /admin/connectors 与 /admin/login 在重构后已不再存在
    # /api/admin/... 是后端 API 空间，必须返回 404
    assert client.get("/api/admin/connectors", headers=admin_headers).status_code == 404
    assert client.post(
        "/api/admin/login", json={"username": "admin", "password": "change-me-now"}
    ).status_code in {404, 405}


def test_http_poll_extracts_task_reference_and_acknowledges():
    messages = HttpPollConnector.extract_messages(
        {
            "messages": [
                {"sender_id": "a", "text": "one"},
                {
                    "sender_id": "b",
                    "text": "two",
                    "conversation_id": "c2",
                    "external_message_id": "x2",
                    "reply_to_task_id": "task-2",
                },
            ]
        }
    )
    assert len(messages) == 2
    assert messages[1].conversation_id == "c2"
    assert messages[1].external_message_id == "x2"
    assert messages[1].reply_to_task_id == "task-2"

    async def run_ack():
        connector = HttpPollConnector(
            9,
            {"ack_url": "https://example.invalid/ack"},
            AsyncMock(),
        )
        response = type("Response", (), {"raise_for_status": lambda self: None})()
        http_client = type(
            "Client",
            (),
            {"post": AsyncMock(return_value=response)},
        )()
        await connector._ack(http_client, {"X-Test": "1"}, ["m1", "m2"])
        http_client.post.assert_awaited_once_with(
            "https://example.invalid/ack",
            headers={"X-Test": "1"},
            json={"connector_id": 9, "message_ids": ["m1", "m2"]},
        )

    asyncio.run(run_ack())


def test_http_poll_rejects_invalid_shapes():
    extract = HttpPollConnector.extract_messages
    assert extract(None) == []
    assert extract("nope") == []
    assert extract([{"text": "x"}]) == []
    assert extract({"text": ""}) == []
    assert extract({"messages": [{"sender_id": "a"}]}) == []
    assert extract({"messages": ["not-a-dict"]}) == []


def test_manager_registry_configures_supported_local_connectors():
    async def run():
        manager = ConnectorManager()
        webhook = IMConnection(
            id=1,
            owner_id=1,
            name="wh",
            platform=ConnectorPlatform.WEBHOOK,
            config_json=dump_connection_config({"inbound_token": "t"}),
        )
        await manager.configure(webhook)
        assert isinstance(manager.get(1), WebhookConnector)

        http = IMConnection(
            id=2,
            owner_id=1,
            name="hp",
            platform=ConnectorPlatform.HTTP,
            config_json=dump_connection_config({"target_url": "http://tests.local/x"}),
        )
        await manager.configure(http)
        connector = manager.get(2)
        assert isinstance(connector, HttpPollConnector)
        assert connector._poll_task is None

        websocket = IMConnection(
            id=3,
            owner_id=1,
            name="ws",
            platform=ConnectorPlatform.WEBSOCKET,
            config_json=dump_connection_config({"auth_token": "k"}),
        )
        await manager.configure(websocket)
        assert isinstance(manager.get(3), WebSocketConnector)
        await manager.stop_all()

    asyncio.run(run())


def test_manager_reconfigure_stops_previous_connector():
    async def run():
        manager = ConnectorManager()
        connection = IMConnection(
            id=1,
            owner_id=1,
            name="wh",
            platform=ConnectorPlatform.WEBHOOK,
            config_json=dump_connection_config({"inbound_token": "t"}),
        )
        await manager.configure(connection)
        previous = manager.get(1)
        previous.stop = AsyncMock()
        await manager.configure(connection)
        previous.stop.assert_awaited_once()

    asyncio.run(run())

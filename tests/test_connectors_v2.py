import asyncio
import json
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

import app.db as database
from app.connectors.http_poll import HttpPollConnector
from app.connectors.ilink import WeChatILinkConnector, qr_svg_data_url
from app.connectors.manager import ConnectorManager
from app.connectors.webhook import WebhookConnector
from app.connectors.websocket import WebSocketConnector
from app.connectors.wecom import WeComConnector
from app.enums import ConnectorPlatform, TaskStatus
from app.models import IMConnection, RequestTask


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


def test_wecom_crypto_roundtrip():
    connector = WeComConnector(
        1,
        {
            "corp_id": "corp",
            "token": "tok",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        },
    )
    crypto = connector._crypto()
    encrypted = crypto.encrypt_message("hello", "nonce")
    encrypt = ET.fromstring(encrypted).findtext("Encrypt", "")
    assert encrypt


def test_webhook_inbound_replies_waiting_task(client, admin_headers):
    config = {"inbound_token": "tok-1", "target_url": ""}
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "Hook", "platform": "webhook", "config": config},
    )
    assert response.status_code == 200
    connection_id = response.json()["id"]

    api_key_response = client.post(
        "/admin/api-keys",
        headers=admin_headers,
        json={
            "name": "hook-key",
            "operator_name": "Op",
            "im_name": "Hook",
            "platform": "webhook",
            "im_config": config,
            "im_connection_id": connection_id,
        },
    )
    assert api_key_response.status_code == 200, api_key_response.text
    api_key_id = api_key_response.json()["id"]

    with database.SessionLocal() as db:
        task = RequestTask(
            api_key_id=api_key_id,
            protocol="openai",
            model="m",
            request_json="{}",
            status=TaskStatus.HUMAN_WAITING,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    inbound = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        headers={"X-Connector-Token": "tok-1"},
        json={"sender_id": "op-1", "text": "/reply\nok\n/done"},
    )
    assert inbound.status_code == 200, inbound.text

    detail = client.get(f"/admin/tasks/{task_id}", headers=admin_headers).json()
    assert detail["status"] == "pseudo_streaming"

    bad = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        headers={"X-Connector-Token": "wrong"},
        json={"text": "x"},
    )
    assert bad.status_code == 403

    missing_text = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        headers={"X-Connector-Token": "tok-1"},
        json={"sender_id": "op-1"},
    )
    assert missing_text.status_code == 400

    wrong_platform = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "X", "platform": "fake", "config": {}},
    )
    fake_id = wrong_platform.json()["id"]
    not_found = client.post(
        f"/connectors/webhook/{fake_id}/inbound",
        headers={"X-Connector-Token": "tok-1"},
        json={"text": "x"},
    )
    assert not_found.status_code == 404


def test_websocket_channel_replies_waiting_task(client, admin_headers):
    config = {"auth_token": "ws-tok"}
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "Ws", "platform": "websocket", "config": config},
    )
    assert response.status_code == 200
    connection_id = response.json()["id"]

    api_key_response = client.post(
        "/admin/api-keys",
        headers=admin_headers,
        json={
            "name": "ws-key",
            "operator_name": "Op",
            "im_name": "Ws",
            "platform": "websocket",
            "im_config": config,
            "im_connection_id": connection_id,
        },
    )
    assert api_key_response.status_code == 200, api_key_response.text
    api_key_id = api_key_response.json()["id"]

    with database.SessionLocal() as db:
        task = RequestTask(
            api_key_id=api_key_id,
            protocol="openai",
            model="m",
            request_json="{}",
            status=TaskStatus.HUMAN_WAITING,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/connectors/ws/{connection_id}?token=ws-tok"
    ) as ws:
        ws.send_text(json.dumps({"sender_id": "u1", "text": "/reply\nhi\n/done"}))

    detail = client.get(f"/admin/tasks/{task_id}", headers=admin_headers).json()
    assert detail["status"] == "pseudo_streaming"


def test_websocket_rejects_invalid_token(client, admin_headers):
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "WsBad", "platform": "websocket", "config": {"auth_token": "ws-tok"}},
    )
    assert response.status_code == 200
    connection_id = response.json()["id"]

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/connectors/ws/{connection_id}?token=bad"
        ) as ws:
            ws.send_text("noop")
    assert exc_info.value.code == 4401


def test_websocket_rejects_missing_server_token(client, admin_headers):
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "WsEmpty", "platform": "websocket", "config": {}},
    )
    connection_id = response.json()["id"]
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/connectors/ws/{connection_id}"):
            pass
    assert exc_info.value.code == 4401


def test_admin_connector_start_stop_health(client, admin_headers):
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "Hook2", "platform": "webhook", "config": {"inbound_token": "t"}},
    )
    assert response.status_code == 200
    cid = response.json()["id"]

    start = client.post(f"/admin/connectors/{cid}/start", headers=admin_headers)
    assert start.status_code == 200
    assert start.json()["platform"] == "webhook"

    health = client.get(f"/admin/connectors/{cid}/health", headers=admin_headers)
    assert health.status_code == 200
    assert health.json()["platform"] == "webhook"

    stop = client.post(f"/admin/connectors/{cid}/stop", headers=admin_headers)
    assert stop.status_code == 200
    assert stop.json() == {"stopped": True}

    missing = client.post("/admin/connectors/99999/start", headers=admin_headers)
    assert missing.status_code == 404


def test_admin_connector_login_endpoints(client, admin_headers):
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "Fake", "platform": "fake", "config": {}},
    )
    assert response.status_code == 200
    cid = response.json()["id"]

    no_login = client.post(f"/admin/connectors/{cid}/login", headers=admin_headers)
    assert no_login.status_code == 400

    no_login_state = client.get(f"/admin/connectors/{cid}/login", headers=admin_headers)
    assert no_login_state.status_code == 400


def test_wecom_callback_url_verify_rejects_bad_signature(client, admin_headers):
    response = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={
            "name": "WC",
            "platform": "wecom",
            "config": {
                "corp_id": "corp",
                "corp_secret": "secret",
                "agent_id": 1,
                "token": "tok",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            },
        },
    )
    assert response.status_code == 200
    cid = response.json()["id"]

    bad = client.get(
        f"/connectors/wecom/{cid}/callback",
        params={
            "msg_signature": "bad",
            "timestamp": "1",
            "nonce": "1",
            "echostr": "x",
        },
    )
    assert bad.status_code == 403

    missing = client.post(
        "/admin/connectors",
        headers=admin_headers,
        json={"name": "Fake2", "platform": "fake", "config": {}},
    )
    fake_id = missing.json()["id"]
    not_wecom = client.get(
        f"/connectors/wecom/{fake_id}/callback",
        params={
            "msg_signature": "x",
            "timestamp": "1",
            "nonce": "1",
            "echostr": "x",
        },
    )
    assert not_wecom.status_code == 404


def test_http_poll_extract_messages_single_object():
    messages = HttpPollConnector.extract_messages(
        {"sender_id": "op-1", "text": "hi", "conversation_id": "c1"}
    )
    assert len(messages) == 1
    assert messages[0].sender_id == "op-1"
    assert messages[0].text == "hi"
    assert messages[0].conversation_id == "c1"


def test_http_poll_extract_messages_messages_array():
    messages = HttpPollConnector.extract_messages(
        {
            "messages": [
                {"sender_id": "a", "text": "one"},
                {"text": "two", "conversation_id": "c2", "external_message_id": "x2"},
            ]
        }
    )
    assert len(messages) == 2
    assert messages[0].text == "one"
    assert messages[1].conversation_id == "c2"
    assert messages[1].external_message_id == "x2"


def test_http_poll_extract_messages_invalid_input():
    extract = HttpPollConnector.extract_messages
    assert extract(None) == []
    assert extract("nope") == []
    assert extract([{"text": "x"}]) == []
    assert extract({"text": ""}) == []
    assert extract({"messages": [{"sender_id": "a"}]}) == []
    assert extract({"messages": ["not-a-dict"]}) == []


def test_manager_configure_instance_types():
    async def run() -> None:
        manager = ConnectorManager()

        webhook = IMConnection(
            id=1,
            name="wh",
            platform=ConnectorPlatform.WEBHOOK,
            config_json='{"inbound_token": "t"}',
        )
        await manager.configure(webhook)
        assert isinstance(manager.get(1), WebhookConnector)

        http = IMConnection(
            id=2,
            name="hp",
            platform=ConnectorPlatform.HTTP,
            config_json='{"target_url": "http://tests.local/x"}',
        )
        await manager.configure(http)
        connector = manager.get(2)
        assert isinstance(connector, HttpPollConnector)
        assert connector._poll_task is None

        websocket = IMConnection(
            id=3,
            name="ws",
            platform=ConnectorPlatform.WEBSOCKET,
            config_json='{"auth_token": "k"}',
        )
        await manager.configure(websocket)
        assert isinstance(manager.get(3), WebSocketConnector)

    asyncio.run(run())


def test_manager_reconfigure_stops_previous_connector():
    async def run() -> None:
        manager = ConnectorManager()
        connection = IMConnection(
            id=1,
            name="wh",
            platform=ConnectorPlatform.WEBHOOK,
            config_json='{"inbound_token": "t"}',
        )
        await manager.configure(connection)
        previous = manager.get(1)
        previous.stop = AsyncMock()
        await manager.configure(connection)
        previous.stop.assert_awaited_once()

    asyncio.run(run())

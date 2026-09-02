"""M4 IM 连接测试：平台目录、加密配置、权限边界、绑定与进站幂等。"""

from __future__ import annotations

import asyncio
import secrets

import pytest
from starlette.websockets import WebSocketDisconnect

import app.core.db as database
from app.core.time import utc_now
from app.domain.enums import (
    DeliveryMode,
    InboundResult,
    InferenceProtocol,
    ReplyStrategy,
    TaskState,
)
from app.repositories.models import (
    ApiKey,
    ImConnection,
    InboundReceipt,
    RequestTask,
    TaskEvent,
)


def _login(client, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_token": "test-token",
            "captcha_code": "test",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_user(client, admin_headers, username: str, password: str = "User-Pass1!") -> dict:
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": username, "display_name": username, "password": password},
    )
    assert created.status_code == 201, created.text
    headers = _login(client, username, password)
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": password, "new_password": "Changed-Pass2!"},
    )
    assert changed.status_code == 200, changed.text
    return headers


def _create_connection(
    client,
    headers,
    *,
    name: str = "webhook-conn",
    platform: str = "webhook",
    config: dict | None = None,
):
    payload = {
        "name": name,
        "platform": platform,
        "config": (
            config
            if config is not None
            else {
                "outbound_url": "https://example.test/hook",
                "outbound_token": "out-token-1",
            }
        ),
    }
    response = client.post("/api/im-connections", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _generated_token(created: dict, field: str = "inbound_token") -> str:
    """取创建响应中一次性返回的网关自签 Token。"""
    token = (created.get("generated_tokens") or {}).get(field)
    assert token and token.startswith("hllm-") and len(token) == len("hllm-") + 43
    return token


def test_platform_catalog_exposes_five_platforms_with_config_schema(client, admin_headers) -> None:
    response = client.get("/api/im-platforms", headers=admin_headers)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert codes == {"wecom_ilink", "wecom_aibot", "webhook", "websocket", "http_poll"}
    websocket = next(item for item in response.json() if item["code"] == "websocket")
    assert websocket["config_schema"][0]["secret"] is True
    assert websocket["config_schema"][0]["name"] == "connection_token"
    wechat = next(item for item in response.json() if item["code"] == "wecom_ilink")
    wecom = next(item for item in response.json() if item["code"] == "wecom_aibot")
    http_poll = next(item for item in response.json() if item["code"] == "http_poll")
    assert wechat["requires_binding"] is True
    assert wechat["binding_command"] is None
    assert wechat["config_schema"] == []
    assert wecom["requires_binding"] is True
    assert wecom["binding_command"] == "connect mycom"
    assert http_poll["requires_binding"] is False


def test_connection_config_is_encrypted_and_secrets_never_echoed(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner")
    created = _create_connection(client, headers)
    token = _generated_token(created)

    # 响应不回显 Secret，只提示已设置；Token 明文仅在 generated_tokens 一次性展示。
    assert created["config"]["inbound_token"] is None
    assert created["config"]["inbound_token_set"] is True
    assert token in response_text(created)
    assert created["generated_tokens"] == {"inbound_token": token}

    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(created["id"]))
        assert row is not None
        assert token not in row.config_ciphertext
        assert row.config_ciphertext.startswith("hlg1.1.")


def test_each_user_can_only_create_one_connection_per_platform(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "single-platform-owner")
    first = _create_connection(client, headers, name="first-webhook")

    duplicate = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": "second-webhook",
            "platform": "webhook",
            "config": {
                "outbound_url": "https://example.test/another-hook",
            },
        },
    )
    assert duplicate.status_code == 409
    assert "每个平台只能创建一条连接" in duplicate.json()["error"]["message"]

    another_platform = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": first["name"],
            "platform": "http_poll",
            "config": {},
        },
    )
    assert another_platform.status_code == 201, another_platform.text
    assert _generated_token(another_platform.json(), "pull_token")


def test_wechat_credentials_can_only_be_saved_by_qr_login(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "wechat-qr-only")
    rejected = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": "微信 iLink",
            "platform": "wecom_ilink",
            "config": {"token": "manual-token"},
        },
    )
    assert rejected.status_code == 400
    assert "扫码绑定" in rejected.json()["error"]["message"]

    created = _create_connection(
        client,
        headers,
        name="微信 iLink",
        platform="wecom_ilink",
        config={},
    )
    changed = client.patch(
        f"/api/im-connections/{created['id']}",
        headers=headers,
        json={"config": {"token": "manual-token"}},
    )
    assert changed.status_code == 400
    assert "扫码绑定" in changed.json()["error"]["message"]


def response_text(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def test_secret_kept_when_update_submits_empty_or_omits(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-2")
    created = _create_connection(client, headers, name="keep-secret")
    connection_id = created["id"]
    token = _generated_token(created)

    omitted = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"name": "renamed"},
    )
    assert omitted.status_code == 200
    assert omitted.json()["name"] == "renamed"

    from app.services.connection_service import ConnectionService

    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == token

    empty = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"config": {"inbound_token": ""}},
    )
    assert empty.status_code == 200
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == token

    # 网关自签 Token 不允许手填：创建与更新提交非空值一律 400。
    manual_create = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": "manual-token",
            "platform": "http_poll",
            "config": {"pull_token": token},
        },
    )
    assert manual_create.status_code == 400
    assert "不允许手动填写" in manual_create.json()["error"]["message"]

    replaced = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"config": {"inbound_token": "hllm-manual-not-allowed"}},
    )
    assert replaced.status_code == 400
    assert "重新生成" in replaced.json()["error"]["message"]
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == token

    # rotate 原子换新：明文只在响应展示，旧 Token 立即失效。
    rotated = client.post(
        f"/api/im-connections/{connection_id}/credentials/inbound_token/rotate",
        headers=headers,
    )
    assert rotated.status_code == 200, rotated.text
    new_token = rotated.json()["token"]
    assert rotated.json()["field"] == "inbound_token"
    assert new_token != token and new_token.startswith("hllm-")
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == new_token
    # 审计 metadata 只记录字段名，不含 Token 明文。
    with database.SessionLocal() as session:
        from app.repositories.models import AuditLog

        rotations = [
            row_log
            for row_log in session.query(AuditLog).filter(
                AuditLog.resource_id == str(connection_id)
            )
            if "credential_rotated" in (row_log.metadata_json or "")
        ]
        assert rotations
        assert all(new_token not in (row_log.metadata_json or "") for row_log in rotations)

    # 非 Token 字段不支持 rotate。
    bad_field = client.post(
        f"/api/im-connections/{connection_id}/credentials/outbound_token/rotate",
        headers=headers,
    )
    assert bad_field.status_code == 400


def test_admin_governance_cannot_create_or_change_credentials(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-3")
    created = _create_connection(client, headers, name="governed")
    connection_id = created["id"]

    assert (
        client.post(
            "/api/im-connections",
            headers=admin_headers,
            json={"name": "admin-conn", "platform": "webhook", "config": {"inbound_token": "x"}},
        ).status_code
        == 403
    )

    # 个人列表接口不再以管理员身份返回他人连接；监管视角走 /api/admin 路由。
    assert client.get("/api/im-connections", headers=admin_headers).json()["items"] == []
    listed = client.get("/api/admin/im-connections", headers=admin_headers).json()
    assert {item["id"] for item in listed["items"]} == {connection_id}
    assert listed["items"][0]["owner_username"] == "conn-owner-3"

    forbidden = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=admin_headers,
        json={"config": {"inbound_token": "hijack"}},
    )
    assert forbidden.status_code == 403
    assert "hijack" not in forbidden.text

    # 管理员可治理启停与检查，但不能绑定或登录。
    assert (
        client.post(f"/api/im-connections/{connection_id}/start", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/im-connections/{connection_id}/health", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/im-connections/{connection_id}/stop", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/im-connections/{connection_id}/binding", headers=admin_headers
        ).status_code
        == 404
    )


def test_delete_blocked_while_enabled_api_key_references_connection(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-4")
    created = _create_connection(client, headers, name="referenced")
    connection_id = int(created["id"])

    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    with database.SessionLocal() as session:
        session.add(
            ApiKey(
                owner_user_id=int(user_id),
                name="im-key",
                key_hash="hash-1",
                key_prefix="sk-imkey",
                delivery_mode=DeliveryMode.IM,
                im_connection_id=connection_id,
                reply_strategy=ReplyStrategy.HUMAN,
                human_timeout_seconds=300,
            )
        )
        session.commit()

    conflict = client.delete(f"/api/im-connections/{connection_id}", headers=headers)
    assert conflict.status_code == 409

    # 停用 Key 的引用同样阻止删除（默认直接阻止并提示引用关系）。
    with database.SessionLocal() as session:
        session.query(ApiKey).filter(ApiKey.id == session.query(ApiKey).first().id).update(
            {"is_enabled": False}
        )
        session.commit()
    still_blocked = client.delete(f"/api/im-connections/{connection_id}", headers=headers)
    assert still_blocked.status_code == 409
    assert "API Key" in still_blocked.json()["error"]["message"]

    # 解除引用后允许删除（IM 模式的 Key 必须同时切换为 Web 入口）。
    with database.SessionLocal() as session:
        session.query(ApiKey).filter(ApiKey.id == session.query(ApiKey).first().id).update(
            {"im_connection_id": None, "delivery_mode": DeliveryMode.WEB}
        )
        session.commit()
    assert client.delete(f"/api/im-connections/{connection_id}", headers=headers).status_code == 204


def test_qr_login_returns_base64_qrcode_and_atomically_saves_binding(
    client, admin_headers, monkeypatch
) -> None:
    """扫码登录：二维码 bytes 转 base64；confirmed 后服务端保存参数并完成绑定。"""
    import base64 as b64

    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-owner")
    created = _create_connection(
        client,
        headers,
        name="ilink-qr",
        platform="wecom_ilink",
        config={},
    )

    class _FakeConnector:
        async def start_login(self):
            return {"qrcode": "QR-DATA", "qrcode_img_content": b"\x89PNG-fake"}

        async def poll_login(self):
            return {
                "status": "confirmed",
                "bot_token": "bot-token-1",
                "baseurl": "https://ilink.example.test",
                "ilink_user_id": "wx-user-1",
            }

    def _fake_login_connector(self, row):
        connector = _FakeConnector()
        self._login_connectors[row.id] = connector
        return connector

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)

    started = client.post(f"/api/im-connections/{created['id']}/login", headers=headers)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["qrcode"] == "QR-DATA"
    assert body["qrcode_img_content"] == b64.b64encode(b"\x89PNG-fake").decode("ascii")

    polled = client.get(f"/api/im-connections/{created['id']}/login", headers=headers)
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "confirmed"
    assert body["bound"] is True
    assert body["trace_id"]
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(created["id"]))
        assert row is not None
        decrypted = ConnectionService().decrypt_config(row)
        assert decrypted.get("token") == "bot-token-1"
        assert decrypted.get("base_url") == "https://ilink.example.test"
        assert row.bound_external_user_id == "wx-user-1"


def test_qr_login_poll_without_start_returns_400_not_500(client, admin_headers) -> None:
    """扫码会话跨请求共享：未先 start 直接 poll 返回 400，而不是未处理 500。"""
    headers = _create_user(client, admin_headers, "qr-poll-first")
    created = _create_connection(
        client, headers, name="ilink-poll", platform="wecom_ilink", config={}
    )
    resp = client.get(f"/api/im-connections/{created['id']}/login", headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "validation_failed"


def test_qr_login_connector_error_mapped_to_domain_error(
    client, admin_headers, monkeypatch
) -> None:
    """连接器抛 ConnectorError（如二维码过期/网络失败）映射为 400/401，不泄露 500。"""
    from app.domain.connections import ERROR_AUTH, ConnectorError
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-err")
    created = _create_connection(
        client, headers, name="ilink-err", platform="wecom_ilink", config={}
    )

    class _FailingConnector:
        async def start_login(self):
            raise ConnectorError(ERROR_AUTH, "iLink 会话已过期，请重新扫码登录")

    def _fake_login_connector(self, row):
        connector = _FailingConnector()
        self._login_connectors[row.id] = connector
        return connector

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)
    resp = client.post(f"/api/im-connections/{created['id']}/login", headers=headers)
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["error"]["code"] == "validation_failed"
    assert "会话已过期" in body["error"]["message"]


def test_binding_code_flow_and_unbound_inbound(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-5")
    created = _create_connection(client, headers, name="binding")
    connection_id = created["id"]
    token = _generated_token(created)

    status = client.get(
        f"/api/im-connections/{connection_id}/binding/status", headers=headers
    ).json()
    assert status["bound"] is False
    assert status["binding_pending"] is False
    assert status["binding_expires_at"] is None

    binding = client.post(f"/api/im-connections/{connection_id}/binding", headers=headers).json()
    assert binding["binding_code"] == "connect webhook"
    assert binding["binding_code"]
    assert binding["expires_at"] is not None
    assert (
        client.get(f"/api/im-connections/{connection_id}/binding/status", headers=headers).json()[
            "binding_pending"
        ]
        is True
    )

    # 未绑定且无绑定码：进站按 unbound 处理。
    inbound = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "m-1", "sender": "u-1", "text": "hello"},
        headers={"X-Webhook-Token": token},
    )
    assert inbound.status_code == 200
    assert inbound.json()["result"] == InboundResult.UNBOUND.value

    bound = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={
            "external_message_id": "m-2",
            "sender": "u-1",
            "text": "hello",
            "binding_code": binding["binding_code"],
        },
        headers={"X-Webhook-Token": token},
    )
    assert bound.json()["result"] == InboundResult.BOUND.value
    assert (
        client.get(f"/api/im-connections/{connection_id}/binding/status", headers=headers).json()[
            "bound"
        ]
        is True
    )

    # 其他发送者不能回复。
    stranger = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "m-3", "sender": "u-2", "text": "hello"},
        headers={"X-Webhook-Token": token},
    )
    assert stranger.json()["result"] == InboundResult.UNBOUND.value


def test_social_connections_must_bind_before_start(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-bind-first")
    wechat = _create_connection(
        client,
        headers,
        name="wechat-unbound",
        platform="wecom_ilink",
        config={},
    )
    wecom = _create_connection(
        client,
        headers,
        name="wecom-unbound",
        platform="wecom_aibot",
        config={"bot_id": "bot-1", "secret": "secret-1"},
    )

    for connection in (wechat, wecom):
        response = client.post(
            f"/api/im-connections/{connection['id']}/start",
            headers=headers,
        )
        assert response.status_code == 400
        assert "绑定成功后才能启用" in response.json()["error"]["message"]


def test_wecom_binding_uses_fixed_command_and_keeps_switch_off(
    client, admin_headers, monkeypatch
) -> None:
    from app.connectors import connection_manager as manager

    headers = _create_user(client, admin_headers, "conn-owner-wecom-bind")
    created = _create_connection(
        client,
        headers,
        name="name-does-not-change-command",
        platform="wecom_aibot",
        config={"bot_id": "bot-2", "secret": "secret-2"},
    )
    starts: list[int] = []

    async def fake_start(row, _config, _inbound) -> None:
        starts.append(row.id)

    monkeypatch.setattr(manager, "start", fake_start)
    response = client.post(
        f"/api/im-connections/{created['id']}/binding",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["binding_code"] == "connect mycom"
    assert response.json()["expires_at"] is not None
    assert starts == [int(created["id"])]
    current = client.get(
        f"/api/im-connections/{created['id']}",
        headers=headers,
    ).json()
    assert current["desired_running"] is False
    assert current["bound"] is False


def test_webhook_inbound_is_idempotent_and_first_reply_wins(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-6")
    created = _create_connection(client, headers, name="idempotent")
    connection_id = int(created["id"])
    token = _generated_token(created)
    client.post(f"/api/im-connections/{connection_id}/binding", headers=headers)
    binding = client.post(f"/api/im-connections/{connection_id}/binding", headers=headers).json()

    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    key_id = _seed_key_and_task(user_id, connection_id)

    client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={
            "external_message_id": "bind-1",
            "sender": "u-1",
            "text": "hi",
            "binding_code": binding["binding_code"],
        },
        headers={"X-Webhook-Token": token},
    )

    first = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-1", "sender": "u-1", "text": "#task_public_1 第一段回复"},
        headers={"X-Webhook-Token": token},
    )
    assert first.json()["result"] == InboundResult.ACCEPTED.value

    duplicate = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-1", "sender": "u-1", "text": "#task_public_1 重复"},
        headers={"X-Webhook-Token": token},
    )
    assert duplicate.json()["result"] == InboundResult.DUPLICATE.value

    late = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-2", "sender": "u-1", "text": "#task_public_1 晚到"},
        headers={"X-Webhook-Token": token},
    )
    assert late.json()["result"] == InboundResult.LATE.value

    with database.SessionLocal() as session:
        task = session.get(RequestTask, key_id)
        assert task.state is TaskState.RESPONSE_READY
        assert "第一段回复" in task.response_payload_json
        receipts = {
            row.external_message_id: row.result_code
            for row in session.query(InboundReceipt).filter(
                InboundReceipt.connection_id == connection_id
            )
        }
        assert receipts["msg-1"] == InboundResult.ACCEPTED.value
        assert receipts["msg-2"] == InboundResult.LATE.value
        events = [
            row.event_type.value
            for row in session.query(TaskEvent).filter(TaskEvent.task_id == task.id)
        ]
        assert "reply_submitted" in events and "reply_rejected_late" in events


def test_webhook_inbound_requires_connection_token(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-7")
    created = _create_connection(client, headers, name="token-guard")
    connection_id = created["id"]

    assert (
        client.post(
            f"/connectors/webhook/{connection_id}/inbound",
            json={"external_message_id": "m-1", "text": "x"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/connectors/webhook/{connection_id}/inbound",
            json={"external_message_id": "m-1", "text": "x"},
            headers={"X-Webhook-Token": "wrong"},
        ).status_code
        == 401
    )


def test_connector_and_health_payload_limits_run_before_parsing(client, monkeypatch) -> None:
    monkeypatch.setattr("app.api.limits.MAX_CONNECTOR_REQUEST_BYTES", 64)

    connector = client.post(
        "/connectors/webhook/999/inbound",
        content=b'{"external_message_id":"m-1","text":"' + b"x" * 200 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert connector.status_code == 413
    assert connector.json()["error"]["code"] == "payload_too_large"

    health = client.request("GET", "/healthz", content=b"x" * 200)
    assert health.status_code == 413
    assert health.json()["error"]["code"] == "payload_too_large"


def test_websocket_rejects_oversized_message(client, admin_headers, monkeypatch) -> None:
    headers = _create_user(client, admin_headers, "ws-size-owner")
    created = _create_connection(
        client,
        headers,
        name="ws-size-limit",
        platform="websocket",
        config={},
    )
    connection_id = created["id"]
    token = _generated_token(created, "connection_token")
    started = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert started.status_code == 200, started.text

    monkeypatch.setattr("app.api.connectors.SessionLocal", database.SessionLocal)
    monkeypatch.setattr("app.api.connectors.MAX_CONNECTOR_WEBSOCKET_MESSAGE_BYTES", 64)
    with client.websocket_connect(f"/connectors/ws/{connection_id}?token={token}") as websocket:
        websocket.send_text("x" * 65)
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_text()
        assert disconnected.value.code == 1009


def test_watchdog_check_disables_abnormal_enabled_connection(client, admin_headers) -> None:
    from app.domain.enums import ConnectionState
    from app.repositories.models import ImConnection

    headers = _create_user(client, admin_headers, "watchdog-owner")
    created = _create_connection(client, headers, name="watchdog-error")
    connection_id = int(created["id"])
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.desired_running = True
        row.state = ConnectionState.ERROR
        row.last_error_code = "network_error"
        row.last_error_message = "连接测试异常"
        session.commit()

    checked = client.post("/api/im-connections/check", headers=headers)
    assert checked.status_code == 200, checked.text
    report = checked.json()
    assert len(report) == 1
    assert report[0]["abnormal"] is True
    assert report[0]["auto_disabled"] is True
    assert report[0]["desired_running"] is False
    with database.SessionLocal() as session:
        assert session.get(ImConnection, connection_id).desired_running is False


def test_http_poll_cursor_reply_and_ack_are_idempotent(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-8")
    created = _create_connection(
        client,
        headers,
        name="poller",
        platform="http_poll",
        config={},
    )
    connection_id = int(created["id"])
    pull_token = _generated_token(created, "pull_token")
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_key_and_task(user_id, connection_id, public_id="task_public_poll")

    from app.services.connection_service import ConnectionService
    from app.services.delivery_service import DeliveryService

    service = ConnectionService()
    # 轮询连接器没有 webhook 入站入口，直接置位绑定身份。
    with database.SessionLocal() as session:
        service.repo.bind_external_user(session, connection_id, "u-1")
        session.commit()
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        connection = session.get(ImConnection, connection_id)
        # 未运行实例：outbox 仍应记录投递包，任务继续在 Web 可见。
        outcome = DeliveryService().deliver_task(session, task=task, connection=connection)
        session.commit()
    assert outcome.delivered is False
    assert outcome.error_code == "connection_offline"
    assert outcome.via_outbox is True

    pulled = client.get(
        f"/connectors/http/{connection_id}/tasks",
        params={"cursor": 0},
        headers={"X-Pull-Token": pull_token},
    ).json()
    assert [item["task_id"] for item in pulled["tasks"]] == ["task_public_poll"]
    assert pulled["cursor"] > 0

    # 重复 cursor 不重复投递新内容。
    second = client.get(
        f"/connectors/http/{connection_id}/tasks",
        params={"cursor": pulled["cursor"]},
        headers={"X-Pull-Token": pull_token},
    ).json()
    assert second["tasks"] == []

    reply = client.post(
        f"/connectors/http/{connection_id}/replies",
        json={
            "external_message_id": "poll-reply-1",
            "task_id": "task_public_poll",
            "text": "轮询回复",
        },
        headers={"X-Pull-Token": pull_token},
    )
    assert reply.json()["result"] == InboundResult.ACCEPTED.value

    assert client.post(
        f"/connectors/http/{connection_id}/ack",
        json={"task_id": "task_public_poll"},
        headers={"X-Pull-Token": pull_token},
    ).json() == {"acked": True}
    assert client.post(
        f"/connectors/http/{connection_id}/ack",
        json={"task_id": "task_public_poll"},
        headers={"X-Pull-Token": pull_token},
    ).json() == {"acked": False}

    with database.SessionLocal() as session:
        assert service.repo.get(session, connection_id) is not None


def _seed_key_and_task(
    owner_user_id: int, connection_id: int, public_id: str = "task_public_1"
) -> int:
    """创建 Key 与 WAITING_HUMAN 任务，返回任务 id。"""
    with database.SessionLocal() as session:
        key = ApiKey(
            owner_user_id=owner_user_id,
            name=f"key-{secrets.token_hex(4)}",
            key_hash=f"hash-{secrets.token_hex(4)}",
            key_prefix="sk-seed1",
            delivery_mode=DeliveryMode.IM,
            im_connection_id=connection_id,
            reply_strategy=ReplyStrategy.HUMAN,
            human_timeout_seconds=300,
        )
        session.add(key)
        session.flush()
        task = RequestTask(
            public_id=public_id,
            owner_user_id=owner_user_id,
            api_key_id=key.id,
            api_key_prefix_snapshot=key.key_prefix,
            requested_model="deepseek-v4-pro",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json='{"messages":[{"role":"user","content":"你好"}]}',
            normalized_request_json='{"messages":[{"role":"user","content":"你好"}],"tools":[]}',
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.IM,
            im_connection_id_snapshot=connection_id,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=utc_now(),
        )
        session.add(task)
        session.commit()
        return task.id


def test_inbound_handler_rejects_unknown_platform_config(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-9")
    response = client.post(
        "/api/im-connections",
        headers=headers,
        json={"name": "bad", "platform": "unknown", "config": {}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"

    invalid = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": "bad-config",
            "platform": "webhook",
            "config": {"outbound_url": "ftp://nope"},
        },
    )
    assert invalid.status_code == 400
    assert "http" in invalid.json()["error"]["message"]


def test_owner_isolation_for_connections(client, admin_headers) -> None:
    headers_a = _create_user(client, admin_headers, "conn-owner-a")
    headers_b = _create_user(client, admin_headers, "conn-owner-b")
    created = _create_connection(client, headers_a, name="private-conn")
    connection_id = created["id"]

    assert client.get("/api/im-connections", headers=headers_b).json()["total"] == 0
    assert client.get(f"/api/im-connections/{connection_id}", headers=headers_b).status_code == 404
    assert (
        client.delete(f"/api/im-connections/{connection_id}", headers=headers_b).status_code == 404
    )


def test_single_connection_can_be_selected_by_multiple_api_keys(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-10")
    created = _create_connection(client, headers, name="shared-conn")
    connection_id = int(created["id"])

    first = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "shared-a", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    second = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "shared-b", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    # 被任一启用 Key 引用时不允许删除连接。
    assert client.delete(f"/api/im-connections/{connection_id}", headers=headers).status_code == 409


def test_start_then_check_does_not_disable_still_starting_connection(
    client, admin_headers, monkeypatch
) -> None:
    """集成回归：POST start 后立即 POST check，连接不能被停用。

    原竞态：start 事务未提交前 manager.start 走独立会话写 starting，触发
    SQLite 写锁；start 看似成功但 state=stopped；看门狗随即判定异常并停用。
    新流程：service.start 先提交 desired_running+starting，再创建监督任务；
    看门狗读取 supervisor_alive，避免对 starting 中的连接误判。
    """
    from app.connectors import connection_manager
    from app.domain.enums import ConnectionState
    from app.repositories.models import ImConnection

    headers = _create_user(client, admin_headers, "start-check-owner")
    created = _create_connection(client, headers, name="start-check")
    connection_id = int(created["id"])

    starts: list[int] = []

    async def fake_start(row, _config, _inbound):
        starts.append(row.id)

        # 模拟 manager.start 实际创建一个长期运行的 supervisor 任务并登记到 _tasks，
        # 看门狗据此判定 supervisor_alive=True，不再误判 starting 状态。
        async def _idle() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

        connection_manager._tasks[row.id] = asyncio.create_task(
            _idle(), name=f"fake-supervisor-{row.id}"
        )

    monkeypatch.setattr(connection_manager, "start", fake_start)

    response = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert response.status_code == 200, response.text
    assert starts == [connection_id]

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.desired_running is True
        assert row.state is ConnectionState.STARTING
        assert row.last_error_code is None

    checked = client.post("/api/im-connections/check", headers=headers)
    assert checked.status_code == 200, checked.text
    report = next(item for item in checked.json() if int(item["id"]) == connection_id)
    assert report["abnormal"] is False
    assert report["auto_disabled"] is False
    assert report["desired_running"] is True
    assert report["runtime"]["supervisor_alive"] is True

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.desired_running is True


def test_start_then_check_disables_when_supervisor_dead_and_state_stuck(
    client, admin_headers, monkeypatch
) -> None:
    """真正的数据不一致：starting 但监督任务已死，依然判为异常。"""
    from app.connectors import connection_manager

    headers = _create_user(client, admin_headers, "stuck-start-owner")
    created = _create_connection(client, headers, name="stuck-start")
    connection_id = int(created["id"])

    async def fake_start(row, _config, _inbound):
        return None  # 创建监督任务后立即返回，未真正启动

    monkeypatch.setattr(connection_manager, "start", fake_start)

    response = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert response.status_code == 200, response.text

    # 模拟 supervisor 任务已经结束：manager 中已无该连接的任务记录，
    # 但数据库状态仍停留在 STARTING（不回写 STOPPED），用于覆盖
    # "state=starting 且 supervisor 已结束" 的漏判分支。
    connection_manager._tasks.pop(connection_id, None)

    checked = client.post("/api/im-connections/check", headers=headers)
    assert checked.status_code == 200, checked.text
    report = next(item for item in checked.json() if int(item["id"]) == connection_id)
    assert report["abnormal"] is True
    assert report["auto_disabled"] is True
    assert report["desired_running"] is False


def test_start_failure_does_not_return_success(client, admin_headers, monkeypatch) -> None:
    """启动失败：补偿事务写 error，接口返回 5xx。"""
    from app.connectors import connection_manager
    from app.domain.connections import ERROR_NETWORK, ConnectorError
    from app.domain.enums import ConnectionState
    from app.repositories.models import ImConnection

    headers = _create_user(client, admin_headers, "start-fail-owner")
    created = _create_connection(client, headers, name="start-fail")
    connection_id = int(created["id"])

    async def fake_start(row, _config, _inbound):
        raise ConnectorError(ERROR_NETWORK, "模拟网络异常")

    monkeypatch.setattr(connection_manager, "start", fake_start)

    response = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert response.status_code == 500, response.text
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.state is ConnectionState.ERROR
        assert row.last_error_code == ERROR_NETWORK
        assert row.last_error_message == "模拟网络异常"

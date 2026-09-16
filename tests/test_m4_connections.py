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


def test_platform_catalog_exposes_platforms_with_config_schema(client, admin_headers) -> None:
    response = client.get("/api/im-platforms", headers=admin_headers)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert codes == {"wecom_ilink", "wecom_aibot", "webhook", "websocket", "http_poll", "lark"}
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
    lark = next(item for item in response.json() if item["code"] == "lark")
    assert lark["requires_binding"] is True
    assert lark["binding_command"] == "connect lark"
    assert lark["config_schema"][0]["name"] == "app_id"
    assert lark["config_schema"][1]["name"] == "app_secret"
    assert lark["config_schema"][1]["secret"] is True


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


def test_delete_connection_with_message_history_succeeds(client, admin_headers) -> None:
    """有投递/入站消息历史的连接也能删除（子行随连接清理，不再 IntegrityError）。"""
    headers = _create_user(client, admin_headers, "conn-owner-history")
    created = _create_connection(client, headers, name="with-history")
    connection_id = int(created["id"])
    with database.SessionLocal() as session:
        session.add(
            InboundReceipt(
                connection_id=connection_id,
                external_message_id="msg-1",
                sender_fingerprint="fp",
                payload_hash="hash",
                result_code="accepted",
            )
        )
        session.commit()

    deleted = client.delete(f"/api/im-connections/{connection_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    with database.SessionLocal() as session:
        remaining = session.query(InboundReceipt).filter_by(connection_id=connection_id).count()
    assert remaining == 0


def test_wecom_aibot_config_change_clears_binding(client, admin_headers) -> None:
    """企微 Bot 编辑保存新配置后：绑定清空、停止运行，必须重新绑定才能启用。"""
    headers = _create_user(client, admin_headers, "conn-owner-aibot")
    created = _create_connection(
        client,
        headers,
        name="aibot",
        platform="wecom_aibot",
        config={"bot_id": "bot-1", "secret": "secret-1"},
    )
    connection_id = int(created["id"])
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "external-user-1"
        row.desired_running = True
        session.commit()

    updated = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"config": {"bot_id": "bot-2"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["bound"] is False
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.bound_external_user_id is None
        assert row.desired_running is False

    # 配置未变化（提交相同值）则不清除绑定。
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "external-user-1"
        session.commit()
    unchanged = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"name": "aibot-renamed"},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["bound"] is True


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


def test_qr_relogin_recovers_watchdog_disabled_connection(
    client, admin_headers, monkeypatch
) -> None:
    """重新扫码必须能恢复被看门狗停用的连接（修复恢复死锁）。

    复现用户场景：会话过期 -> state=auth_required -> 看门狗停用
    （desired_running=0）-> 用户重新扫码确认。旧逻辑下 poll_login 因
    desired_running=0 跳过重启，状态停留 auth_required，手动启用又被
    start 的状态校验拒绝——用户重扫多少次都被堵死。修复后：确认时清除
    失效错误状态并恢复启用，连接直接拉起。
    """
    from app.connectors import connection_manager as manager
    from app.domain.enums import ConnectionState
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-relock")
    created = _create_connection(
        client, headers, name="ilink-relock", platform="wecom_ilink", config={}
    )
    connection_id = int(created["id"])

    class _FakeConnector:
        async def start_login(self):
            return {"qrcode": "QR-DATA", "qrcode_img_content": b"\x89PNG-fake"}

        async def poll_login(self):
            return {
                "status": "confirmed",
                "bot_token": "bot-token-2",
                "baseurl": "",
                "ilink_user_id": "wx-user-2",
            }

    def _fake_login_connector(self, row):
        connector = _FakeConnector()
        self._login_connectors[row.id] = connector
        return connector

    async def _fake_manager_start(row, _config, _inbound) -> None:
        return None

    async def _fake_manager_stop(_connection_id) -> None:
        return None

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)
    monkeypatch.setattr(manager, "start", _fake_manager_start)
    monkeypatch.setattr(manager, "stop", _fake_manager_stop)

    # 等价「会话过期 + 看门狗已停用」的现场。
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "wx-user-1"
        row.desired_running = False
        row.state = ConnectionState.AUTH_REQUIRED
        row.last_error_code = "auth_required"
        row.last_error_message = "iLink 会话已过期，请重新扫码登录"
        session.commit()

    started = client.post(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert started.status_code == 200, started.text
    polled = client.get(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert polled.status_code == 200
    assert polled.json()["status"] == "confirmed"

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.state is ConnectionState.STOPPED or row.state is ConnectionState.ONLINE
        assert row.last_error_code is None
        assert row.desired_running is True


def test_qr_relogin_keeps_user_stopped_connection_stopped(
    client, admin_headers, monkeypatch
) -> None:
    """用户主动停用的连接重扫码后保持停用，但清除失效错误状态。

    与看门狗停用不同：state=STOPPED + desired_running=0 是用户显式停止，
    重新扫码只更新凭据并清理状态，不代用户启用；随后手动 start 不再被
    状态校验拒绝。
    """
    from app.domain.enums import ConnectionState
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-restop")
    created = _create_connection(
        client, headers, name="ilink-restop", platform="wecom_ilink", config={}
    )
    connection_id = int(created["id"])

    class _FakeConnector:
        async def start_login(self):
            return {"qrcode": "QR-DATA", "qrcode_img_content": b"\x89PNG-fake"}

        async def poll_login(self):
            return {
                "status": "confirmed",
                "bot_token": "bot-token-3",
                "baseurl": "",
                "ilink_user_id": "wx-user-3",
            }

    def _fake_login_connector(self, row):
        connector = _FakeConnector()
        self._login_connectors[row.id] = connector
        return connector

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "wx-user-1"
        row.desired_running = False
        row.state = ConnectionState.STOPPED
        session.commit()

    started = client.post(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert started.status_code == 200, started.text
    polled = client.get(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert polled.status_code == 200
    assert polled.json()["status"] == "confirmed"

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        assert row.state is ConnectionState.STOPPED
        assert row.desired_running is False
        assert row.last_error_code is None


def test_qr_login_poll_reports_bound_when_binding_finished_elsewhere(
    client, admin_headers, monkeypatch
) -> None:
    """轮询未确认时附带当前绑定态：绑定已在别处完成时前端可收敛到成功态。

    复现用户场景：扫码绑定已成功且连接被自动启用，但浏览器仍持有过期的
    未绑定状态并发起新登录会话——此时轮询应返回 bound=true，前端据此
    收起无效二维码，而不是让用户扫一张没有任何反应的码。
    """
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-bound-elsewhere")
    created = _create_connection(
        client,
        headers,
        name="ilink-bound-elsewhere",
        platform="wecom_ilink",
        config={},
    )
    connection_id = int(created["id"])

    class _FakeConnector:
        async def start_login(self):
            return {"qrcode": "QR-DATA", "qrcode_img_content": b"\x89PNG-fake"}

        async def poll_login(self):
            return {"status": "wait"}

    def _fake_login_connector(self, row):
        connector = _FakeConnector()
        self._login_connectors[row.id] = connector
        return connector

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)

    # 绑定已在别处完成（等价自动启用/另一标签页 confirmed 落库）。
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "wx-user-1"
        session.commit()

    started = client.post(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert started.status_code == 200, started.text

    polled = client.get(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "wait"
    assert body["bound"] is True

    # 清理残留登录态连接器，避免污染共享的单例 _login_connectors。
    from app.api import connections as _connections_api

    _connections_api._service._drop_login_connector(connection_id)


def test_qr_login_poll_reports_unbound_when_not_yet_bound(
    client, admin_headers, monkeypatch
) -> None:
    """未绑定连接的轮询返回 bound=false，前端继续等待扫码。"""
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-unbound")
    created = _create_connection(
        client, headers, name="ilink-unbound", platform="wecom_ilink", config={}
    )

    class _FakeConnector:
        async def start_login(self):
            return {"qrcode": "QR-DATA", "qrcode_img_content": b"\x89PNG-fake"}

        async def poll_login(self):
            return {"status": "wait"}

    def _fake_login_connector(self, row):
        connector = _FakeConnector()
        self._login_connectors[row.id] = connector
        return connector

    monkeypatch.setattr(ConnectionService, "_login_connector", _fake_login_connector)
    assert (
        client.post(f"/api/im-connections/{created['id']}/login", headers=headers).status_code
        == 200
    )

    polled = client.get(f"/api/im-connections/{created['id']}/login", headers=headers)
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "wait"
    assert body["bound"] is False

    # 清理残留登录态连接器，避免污染共享的单例 _login_connectors。
    from app.api import connections as _connections_api

    _connections_api._service._drop_login_connector(int(created["id"]))


def test_start_sends_bind_welcome_for_qr_login_platform(client, admin_headers, monkeypatch) -> None:
    """首次扫码绑定后启用连接：补发欢迎消息（绑定发生在浏览器侧，IM 无确认）。

    复现用户流程：创建连接 -> 扫码绑定（desired_running 仍为 False）-> start。
    start 成功后必须调度欢迎消息，否则用户在 IM 侧收不到任何主动通知。
    """
    from app.connectors import connection_manager as manager
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "qr-welcome")
    created = _create_connection(
        client, headers, name="ilink-welcome", platform="wecom_ilink", config={}
    )
    connection_id = int(created["id"])

    # 扫码绑定结果直接落库（等价于 poll_login confirmed 之后的状态）。
    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "wx-user-1"
        session.commit()

    scheduled: list[tuple[int, str]] = []

    def _fake_schedule(self, cid: int, target: str) -> None:
        scheduled.append((cid, target))

    async def _fake_manager_start(row, _config, _inbound) -> None:
        return None

    monkeypatch.setattr(ConnectionService, "_schedule_bind_welcome", _fake_schedule)
    monkeypatch.setattr(manager, "start", _fake_manager_start)
    response = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert response.status_code == 200, response.text
    assert scheduled == [(connection_id, "wx-user-1")]


def test_start_skips_bind_welcome_for_chat_command_platforms(
    client, admin_headers, monkeypatch
) -> None:
    """lark 等聊天命令绑定平台不补发欢迎：绑定确认已在聊天内回复，避免重复。"""
    from app.connectors import connection_manager as manager
    from app.services.connection_service import ConnectionService

    headers = _create_user(client, admin_headers, "lark-no-welcome")
    created = _create_connection(
        client,
        headers,
        name="lark-no-welcome",
        platform="lark",
        config={"app_id": "cli_x", "app_secret": "sec_x"},
    )
    connection_id = int(created["id"])

    with database.SessionLocal() as session:
        row = session.get(ImConnection, connection_id)
        row.bound_external_user_id = "ou_x"
        session.commit()

    scheduled: list[tuple[int, str]] = []

    def _fake_schedule(self, cid: int, target: str) -> None:
        scheduled.append((cid, target))

    async def _fake_manager_start(row, _config, _inbound) -> None:
        return None

    monkeypatch.setattr(ConnectionService, "_schedule_bind_welcome", _fake_schedule)
    monkeypatch.setattr(manager, "start", _fake_manager_start)
    response = client.post(f"/api/im-connections/{connection_id}/start", headers=headers)
    assert response.status_code == 200, response.text
    assert scheduled == []


def test_send_bind_welcome_pushes_command_help_when_connector_online() -> None:
    """连接器就绪后欢迎消息推送给绑定用户，文案包含指令清单。"""
    from app.services.connection_service import ConnectionService

    sent: list[tuple[str, str]] = []

    class _FakeConnector:
        async def send_reply_text(self, target: str, text: str) -> None:
            sent.append((target, text))

    class _FakeManager:
        def get_instance(self, connection_id: int):
            return _FakeConnector()

    asyncio.run(ConnectionService()._send_bind_welcome(7, "wx-user-1", _FakeManager()))

    assert len(sent) == 1
    target, text = sent[0]
    assert target == "wx-user-1"
    assert text.startswith("连接绑定成功，可以开始接收任务。")
    assert "/ans" in text and "/commit" in text and "/page" in text


def test_send_bind_welcome_skips_when_connector_never_online(caplog) -> None:
    """连接一直未上线（如启动失败）时静默跳过：只记 warning，不抛错。"""
    from app.services.connection_service import ConnectionService

    class _FakeManager:
        def get_instance(self, connection_id: int):
            return None

    with caplog.at_level("WARNING", logger="app.services.connection_service"):
        asyncio.run(
            ConnectionService()._send_bind_welcome(
                1, "wx-x", _FakeManager(), tries=3, interval=0.01
            )
        )
    assert any("bind welcome skipped" in rec.message for rec in caplog.records)


def test_ilink_classify_no_context_token_is_delivery_error() -> None:
    """NoContextTokenError 是协议限制（用户未发过消息），不是网络错误。"""
    from app.connectors.implementations.wecom_ilink import _classify

    class NoContextTokenError(Exception):
        pass

    err = _classify(NoContextTokenError())
    assert err.code == "delivery_failed"
    assert "尚未发消息" in err.message


def test_ilink_deliver_falls_back_to_bound_user() -> None:
    """投递包未显式指定目标时，必须回退到连接绑定的外部用户。

    回归：DeliveryService.build_envelope 不填 reply_to_external_id，而
    wecom_ilink.deliver 缺少绑定用户回退时，任务投递必然失败
    （「缺少投递目标」→ delivery_failed），微信连接永远收不到任务。
    """
    from app.connectors.base import ConnectorContext, DeliveryEnvelope
    from app.connectors.implementations.wecom_ilink import WeComIlinkConnector

    pushed: list[tuple[str, str]] = []

    class _FakeClient:
        def get_context_token(self, user_id: str) -> str | None:
            return "ctx-token-1"  # 已缓存（用户发过消息）

        def push(self, to: str, text: str) -> str:
            pushed.append((to, text))
            return "client-id-1"

    ctx = ConnectorContext(
        connection_id=98,
        owner_user_id=1,
        name="ilink-deliv",
        platform="wecom_ilink",
        config={"token": "t-1"},
        bound_external_user_id="wx-bound-user",
    )
    connector = WeComIlinkConnector(ctx)
    connector._client = _FakeClient()

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    connector._thread = _AliveThread()  # 连接在线（监听线程存活）
    envelope = DeliveryEnvelope(
        task_public_id="t_deliv1",
        requested_model="m",
        prompt_text="问题全文",
        owner_user_id=1,
        messages=["提示条", "内容条"],
    )

    asyncio.run(connector.deliver(envelope))

    assert pushed == [("wx-bound-user", "提示条"), ("wx-bound-user", "内容条")]


def test_ilink_session_expired_stops_monitor_and_reports_auth() -> None:
    """会话过期必须终止监听线程并上报 auth_required。

    SDK 对过期会话以 5 分钟为周期空转轮询且永不退出；连接器必须在过期
    回调里主动停止 client，让 wait_closed 返回、监督任务读到 auth 错误
    转入 auth_required 等待重新扫码，而不是带着死会话一直显示在线。
    """
    from app.connectors.base import ConnectorContext
    from app.connectors.implementations.wecom_ilink import WeComIlinkConnector

    class _FakeMonitorOptions:
        def __init__(self, on_session_expired=None, on_error=None, **_kwargs):
            self.on_session_expired = on_session_expired
            self.on_error = on_error

    class _FakeClient:
        def __init__(self, **_kwargs):
            self.stopped = False
            self.monitor_options: _FakeMonitorOptions | None = None

        def stop(self) -> None:
            self.stopped = True

        def monitor(self, _handler, options) -> None:
            self.monitor_options = options
            # 模拟 SDK 检测到会话过期：触发回调后立刻返回（线程随之结束）。
            if options.on_session_expired is not None:
                options.on_session_expired()

    import openilink

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(openilink, "Client", _FakeClient)
    monkeypatch.setattr(openilink, "MonitorOptions", _FakeMonitorOptions)
    try:
        connector = WeComIlinkConnector(
            ConnectorContext(
                connection_id=99,
                owner_user_id=1,
                name="ilink-expired",
                platform="wecom_ilink",
                config={"token": "t-1"},
            )
        )
        asyncio.run(connector.start())
        # 监听线程应在过期后很快退出：wait_closed 可完成而非永久挂起。
        asyncio.run(asyncio.wait_for(connector.wait_closed(), timeout=5))
        error = connector.last_error()
        assert error is not None and error.is_auth
        assert "重新扫码" in error.message
    finally:
        monkeypatch.undo()


def test_ilink_client_created_with_no_proxy_doer(client, admin_headers, monkeypatch) -> None:
    """iLink client 必须使用直连 HTTPDoer，绕过系统代理。

    iLink 是微信国内直连端点；代理（VPN）会破坏 TLS 且 SDK 静默吞掉
    网络错误，表现为连接假在线收不到任何消息。开启系统代理的环境下，
    start/start_login 创建的 client 都必须强制直连。
    """
    import openilink

    created_clients: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            created_clients.append(kwargs)

        def fetch_qr_code(self):
            return _FakeResponse()

    class _FakeResponse:
        qrcode = "qr-1"
        qrcode_img_content = b""

    monkeypatch.setattr(openilink, "Client", _FakeClient)
    headers = _create_user(client, admin_headers, "ilink-noproxy")
    created = _create_connection(
        client, headers, name="ilink-noproxy", platform="wecom_ilink", config={}
    )
    connection_id = int(created["id"])

    # 扫码登录路径创建的 client
    resp = client.post(f"/api/im-connections/{connection_id}/login", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(created_clients) == 1
    assert created_clients[0]["http_doer"] is not None
    from app.connectors.implementations.wecom_ilink import _NoProxyHTTPDoer

    assert isinstance(created_clients[0]["http_doer"], _NoProxyHTTPDoer)


def test_ilink_client_cdn_upload_bypasses_system_proxy() -> None:
    """iLink client 的 CDN 上传必须直连（绕过系统代理）。

    回归：SDK 的 _do_cdn_post 用 urlopen 直发（遵循系统代理），代理
    环境下 TLS 握手被破坏 -> /file 的 CDN 上传 100% 失败
    （SSL: UNEXPECTED_EOF_WHILE_READING）。本连接器创建的 client 必须
    覆写 CDN 通路为直连 opener。
    """
    from app.connectors.implementations.wecom_ilink import _create_client

    client = _create_client(token="t-1")

    # 1) _do_cdn_post 已被覆写为直连实现（不再走 SDK 的 urlopen 原始版本）
    from app.connectors.implementations.wecom_ilink import _NO_PROXY_OPENER, _no_proxy_cdn_post

    assert client._do_cdn_post.__func__ is _no_proxy_cdn_post

    # 2) 直连 opener 不带任何代理 handler（空代理配置下 build_opener 不装配
    #    ProxyHandler，与默认 urlopen 的系统代理通路相对，即直连）
    proxy_handlers = [h for h in _NO_PROXY_OPENER.handlers if type(h).__name__ == "ProxyHandler"]
    assert not proxy_handlers, "直连 opener 不应装配代理 handler"


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

    logs = client.get(
        "/api/logs?category=http&event=http.access",
        headers=admin_headers,
    )
    assert logs.status_code == 200
    websocket_log = next(
        item
        for item in logs.json()["items"]
        if item["context"]
        and item["context"].get("kind") == "ws"
        and item["context"].get("path", "").startswith("/connectors/ws/")
    )
    assert websocket_log["request_id"]


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
            api_key_name_snapshot=key.name,
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

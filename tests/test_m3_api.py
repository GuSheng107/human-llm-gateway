"""M3 注册、邀请码、受限会话、账号与用户治理 API 测试。"""

from __future__ import annotations

import secrets

import app.core.db as database
from app.core.time import utc_now
from app.domain.enums import DeliveryMode, InferenceProtocol, ReplyStrategy, TaskState
from app.repositories.models import ApiKey, AuditLog, RequestTask, User
from tests.conftest import ADMIN_PASSWORD


def _login(client, username: str, password: str) -> tuple[dict, dict]:
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def _create_invitation(client, admin_headers, *, max_uses: int = 1) -> dict:
    response = client.post(
        "/api/invitations",
        headers=admin_headers,
        json={"note": "测试邀请", "max_uses": max_uses},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_restricted_session_only_allows_me_logout_and_password(client) -> None:
    body, headers = _login(client, "admin", ADMIN_PASSWORD)
    assert body["must_change_password"] is True
    assert body["capabilities"] == ["account.password.change"]
    assert client.get("/api/invitations", headers=headers).status_code == 403
    assert (
        client.patch(
            "/api/account/profile", headers=headers, json={"display_name": "Blocked"}
        ).status_code
        == 403
    )

    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "New-Secure-Admin5!",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert set(changed.json()["capabilities"]) == {
        "account.password.change",
        "account.profile.update",
        "invitation.manage",
        "user.manage",
    }
    assert client.get("/api/invitations", headers=headers).status_code == 200


def test_invitation_lifecycle_and_atomic_registration(client, admin_headers) -> None:
    invitation = _create_invitation(client, admin_headers)
    assert invitation["code"]
    invitation_id = invitation["id"]

    listed = client.get("/api/invitations", headers=admin_headers).json()
    assert listed["total"] == 1
    assert "code" not in listed["items"][0]

    updated = client.patch(
        f"/api/invitations/{invitation_id}",
        headers=admin_headers,
        json={"note": "更新后的备注", "max_uses": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["note"] == "更新后的备注"
    assert (
        client.delete(f"/api/invitations/{invitation_id}", headers=admin_headers).status_code == 409
    )

    registered = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"].lower(),
            "username": "alice",
            "display_name": "Alice 用户",
            "password": "Alice-Pass1!",
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["role"] == "user"
    assert registered.json()["must_change_password"] is False
    login, _headers = _login(client, "alice", "Alice-Pass1!")
    assert login["username"] == "alice"

    exhausted = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"],
            "username": "bob",
            "display_name": "Bob",
            "password": "Bob-Pass2!",
        },
    )
    assert exhausted.status_code == 400
    assert exhausted.json()["error"]["code"] == "invalid_invitation"

    revoked = client.post(f"/api/invitations/{invitation_id}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    deleted = client.delete(f"/api/invitations/{invitation_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/invitations/{invitation_id}", headers=admin_headers).status_code == 404
    with database.SessionLocal() as session:
        registered_user = session.query(User).filter(User.username == "alice").one()
        assert registered_user.registered_via_invitation_id == int(invitation_id)
        actions = {row.action for row in session.query(AuditLog).all()}
        assert {"invitation.created", "invitation.deleted", "user.created"} <= actions


def test_failed_registration_does_not_consume_invitation(client, admin_headers) -> None:
    invitation = _create_invitation(client, admin_headers)
    invalid = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"],
            "username": "无效用户名",
            "display_name": "Invalid",
            "password": "Valid-Pass3!",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "validation_failed"

    valid = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"],
            "username": "after-failure",
            "display_name": "After Failure",
            "password": "Valid-Pass3!",
        },
    )
    assert valid.status_code == 201, valid.text


def test_user_account_and_disable_transaction(client, admin_headers) -> None:
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": "managed", "display_name": "Managed User"},
    )
    assert created.status_code == 201, created.text
    created_body = created.json()
    temporary_password = created_body["temporary_password"]
    user_id = int(created_body["id"])
    assert temporary_password

    login, user_headers = _login(client, "managed", temporary_password)
    assert login["must_change_password"] is True
    assert client.get("/api/users", headers=user_headers).status_code == 403
    changed = client.post(
        "/api/account/password",
        headers=user_headers,
        json={
            "current_password": temporary_password,
            "new_password": "Managed-User-Pass4!",
        },
    )
    assert changed.status_code == 200
    profile = client.patch(
        "/api/account/profile",
        headers=user_headers,
        json={"display_name": "Managed Renamed"},
    )
    assert profile.status_code == 200
    assert client.get("/api/users", headers=user_headers).status_code == 403

    with database.SessionLocal() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.active_task_count = 1
        key = ApiKey(
            owner_user_id=user_id,
            name="managed-key",
            key_hash="hash-managed-key",
            key_prefix="hlg_managed",
            delivery_mode=DeliveryMode.WEB,
            reply_strategy=ReplyStrategy.HUMAN,
            human_timeout_seconds=300,
        )
        session.add(key)
        session.flush()
        task = RequestTask(
            public_id=f"task_{secrets.token_hex(8)}",
            owner_user_id=user_id,
            api_key_id=key.id,
            api_key_prefix_snapshot=key.key_prefix,
            requested_model="human-gateway",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json="{}",
            normalized_request_json="{}",
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.WEB,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=utc_now(),
        )
        session.add(task)
        session.commit()
        key_id, task_id = key.id, task.id

    detail = client.get(f"/api/users/{user_id}", headers=admin_headers).json()
    assert detail["impact"] == {
        "active_sessions": 1,
        "enabled_api_keys": 1,
        "active_tasks": 1,
    }
    disabled = client.patch(
        f"/api/users/{user_id}", headers=admin_headers, json={"is_active": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=user_headers).status_code == 401

    with database.SessionLocal() as session:
        user = session.get(User, user_id)
        key = session.get(ApiKey, key_id)
        task = session.get(RequestTask, task_id)
        assert user is not None and user.active_task_count == 0
        assert key is not None and key.is_enabled is False
        assert task is not None and task.state is TaskState.CANCELLED
        assert task.slot_released_at is not None

    enabled = client.patch(f"/api/users/{user_id}", headers=admin_headers, json={"is_active": True})
    assert enabled.status_code == 200
    with database.SessionLocal() as session:
        assert session.get(ApiKey, key_id).is_enabled is False


def test_admin_constraints_reset_and_audit_redaction(client, admin_headers) -> None:
    admin_id = client.get("/api/auth/me", headers=admin_headers).json()["id"]
    invalid_status = client.patch(
        f"/api/users/{admin_id}",
        headers=admin_headers,
        json={"is_active": None, "password": "must-not-appear-in-error"},
    )
    assert invalid_status.status_code == 422
    assert invalid_status.json()["error"]["code"] == "schema_error"
    assert "must-not-appear-in-error" not in invalid_status.text
    assert (
        client.patch(
            f"/api/users/{admin_id}", headers=admin_headers, json={"is_active": False}
        ).status_code
        == 403  # 管理员不能禁用自己
    )
    assert (
        client.patch(
            f"/api/users/{admin_id}", headers=admin_headers, json={"role": "user"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/users/{admin_id}/reset-password", headers=admin_headers, json={}
        ).status_code
        == 403
    )

    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "reset-user",
            "display_name": "Reset User",
            "password": "Initial-Reset-User6!",
        },
    ).json()
    assert created["temporary_password"] is None
    result = client.post(
        f"/api/users/{created['id']}/reset-password", headers=admin_headers, json={}
    )
    assert result.status_code == 200
    generated = result.json()["temporary_password"]
    assert generated
    login, _headers = _login(client, "reset-user", generated)
    assert login["must_change_password"] is True

    with database.SessionLocal() as session:
        encoded = "\n".join(row.metadata_json or "" for row in session.query(AuditLog).all())
        assert generated not in encoded
        assert "Initial-Reset-User6!" not in encoded


def test_username_length_is_bounded_at_schema_boundary(client, admin_headers) -> None:
    # 模式上限 64，超过应在边界返回 422 而非落入服务层。
    too_long = "a" * 65
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": too_long, "display_name": "Too Long", "password": None},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_error"


def test_admin_cannot_disable_self(client, admin_headers) -> None:
    me = client.get("/api/auth/me", headers=admin_headers).json()
    response = client.patch(
        f"/api/users/{me['id']}", headers=admin_headers, json={"is_active": False}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_email_validation_on_registration(client, admin_headers) -> None:
    invitation = _create_invitation(client, admin_headers)

    invalid = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"],
            "username": "email-bad",
            "display_name": "Email Bad",
            "password": "Email-Pass1!",
            "email": "not-an-email",
        },
    )
    assert invalid.status_code == 400

    valid = client.post(
        "/api/auth/register",
        json={
            "invitation_code": invitation["code"],
            "username": "email-ok",
            "display_name": "Email Ok",
            "password": "Email-Pass1!",
            "email": "user@example.com",
        },
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["email"] == "user@example.com"

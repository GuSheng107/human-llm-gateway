"""认证 API 测试。"""

from __future__ import annotations

import unicodedata

import app.core.db as database
from app.services.user_service import UserService
from tests.conftest import ADMIN_PASSWORD


def test_login_and_me(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert body["must_change_password"] is True
    assert body["capabilities"] == ["account.password.change"]
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["capabilities"] == ["account.password.change"]


def test_login_wrong_password(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "nope-nope-nope",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert response.status_code == 401


def test_me_without_token(client) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_logout(client, admin_headers) -> None:
    response = client.post("/api/auth/logout", headers=admin_headers)
    assert response.status_code == 204
    assert client.get("/api/auth/me", headers=admin_headers).status_code == 401


def test_unicode_password_uses_same_nfc_form_for_create_and_login(client) -> None:
    decomposed = unicodedata.normalize("NFD", "Passw0rd!é")
    with database.SessionLocal() as session:
        UserService().create_admin(
            session,
            username="unicode-admin",
            display_name="Unicode Admin",
            password=decomposed,
        )
        session.commit()

    response = client.post(
        "/api/auth/login",
        json={
            "username": "unicode-admin",
            "password": decomposed,
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert response.status_code == 200


def test_healthz(client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"

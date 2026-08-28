"""认证 API 测试。"""

from __future__ import annotations

import unicodedata

import app.core.db as database
from app.services.user_service import UserService
from tests.conftest import ADMIN_PASSWORD


def test_login_and_me(client) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_login_wrong_password(client) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"}
    )
    assert response.status_code == 401


def test_me_without_token(client) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_logout(client, admin_headers) -> None:
    response = client.post("/api/auth/logout", headers=admin_headers)
    assert response.status_code == 204
    assert client.get("/api/auth/me", headers=admin_headers).status_code == 401


def test_unicode_password_uses_same_nfc_form_for_create_and_login(client) -> None:
    decomposed = unicodedata.normalize("NFD", "é" * 15)
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
        json={"username": "unicode-admin", "password": decomposed},
    )
    assert response.status_code == 200


def test_healthz(client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"

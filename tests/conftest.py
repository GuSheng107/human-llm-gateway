import base64
import os
import secrets

# 必须在导入 app 前设置合法 APP_SECRET（config 在导入时校验）。
if not os.environ.get("APP_SECRET"):
    os.environ["APP_SECRET"] = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    )
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "Admin-Pass1!")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.db as database
from app.api import create_app

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
FULL_ADMIN_PASSWORD = "Updated-Admin-Pass2!"


@pytest.fixture(autouse=True)
def bypass_captcha(monkeypatch):
    """测试环境绕过图形验证码校验（验证码正确性由独立单元测试覆盖）。"""
    monkeypatch.setattr("app.api.auth.verify_captcha", lambda token, code: True)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    engine.dispose()


@pytest.fixture()
def admin_headers(client):
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "captcha_token": "test-token",
            "captcha_code": "test",
        },
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": ADMIN_PASSWORD, "new_password": FULL_ADMIN_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    # 调试：确认 must_change_password 已清除
    from sqlalchemy import select

    with database.SessionLocal() as s2:
        from app.repositories.models import User as _U

        u = s2.execute(select(_U).where(_U.username == "admin")).scalar_one()
        print("DEBUG admin must_change_password=", u.must_change_password)
    return headers


from types import SimpleNamespace


@pytest.fixture()
def created_user(client, admin_headers):
    import secrets as _secrets

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.repositories.models import User

    username = f"u-{_secrets.token_hex(4)}"
    password = "User-Pass1!"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": username, "display_name": username, "password": password},
    )
    assert created.status_code == 201, created.text
    resp = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # 管理员创建的用户默认 must_change_password=true；为后续端点可用需先改密。
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": password, "new_password": "Changed-Pass1!"},
    )
    assert changed.status_code == 200, changed.text
    resp = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.username == username)).scalar_one()
    return SimpleNamespace(headers=headers, user_id=user.id, username=username, password=password)


@pytest.fixture()
def created_key(client, created_user):
    resp = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={"name": "k", "delivery_mode": "web", "reply_strategy": "human"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return SimpleNamespace(
        id=int(data["id"]),
        plaintext=data["plaintext"],
        owner_user_id=created_user.user_id,
    )

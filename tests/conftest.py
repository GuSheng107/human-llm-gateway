import base64
import os
import secrets

# 必须在导入 app 前设置合法 APP_SECRET（config 在导入时校验）。
if not os.environ.get("APP_SECRET"):
    os.environ["APP_SECRET"] = (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    )
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery-staple")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.db as database
from app.api import create_app

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


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
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

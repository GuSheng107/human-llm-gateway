import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as database
from app.api import create_app
from app.models import Base


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def admin_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "change-me-now"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def user_headers(client, admin_headers):
    created = client.post(
        "/admin/users",
        headers=admin_headers,
        json={
            "username": "operator",
            "display_name": "测试操作员",
            "password": "operator-password",
        },
    )
    assert created.status_code == 200, created.text
    login = client.post(
        "/auth/login",
        json={"username": "operator", "password": "operator-password"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}

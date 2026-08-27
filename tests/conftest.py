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
    response = client.post("/admin/login", json={"username": "admin", "password": "change-me-now"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

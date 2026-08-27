import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import create_app
from app.config import get_settings
from app.models import AdminUser, Base
from app.models import RequestTask
from app.enums import TaskStatus
import app.db as database
from app.connectors.sidecar import WeChatSidecarConnector
from app.dsl import ParsedEvent
from app.enums import EventKind
from app.llm import LLMAdapter
from app.security import verify_password


def test_fresh_sqlite_database_is_created_and_admin_is_seeded(tmp_path, monkeypatch):
    database_path = tmp_path / "nested" / "gateway.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ADMIN_USERNAME", "bootstrap-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()

    original_engine = database.engine
    original_session_local = database.SessionLocal
    test_engine = create_engine(database_url, connect_args={"check_same_thread": False})
    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        with TestClient(create_app()) as fresh_client:
            assert database_path.is_file()
            response = fresh_client.post(
                "/admin/login",
                json={"username": "bootstrap-admin", "password": "bootstrap-password"},
            )
            assert response.status_code == 200

        with database.SessionLocal() as db:
            admin = db.execute(
                select(AdminUser).where(AdminUser.username == "bootstrap-admin")
            ).scalar_one()
            assert admin.password_hash != "bootstrap-password"
            assert verify_password("bootstrap-password", admin.password_hash)
    finally:
        test_engine.dispose()
        database.engine = original_engine
        database.SessionLocal = original_session_local
        get_settings.cache_clear()


def create_key(client, headers, **overrides):
    payload = {
        "name": "human-key",
        "operator_name": "Alice",
        "im_name": "Local Fake",
        "platform": "fake",
        "route_name": "human-route",
        "model_name": "human-default",
        **overrides,
    }
    response = client.post("/admin/api-keys", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_configures_provider_route_and_key_binding(client, admin_headers, monkeypatch):
    called: dict[str, str] = {}

    async def fake_complete(self, provider, model, messages, app_secret):
        called["model"] = model
        return [ParsedEvent(EventKind.FINAL, "admin selected")]

    monkeypatch.setattr(LLMAdapter, "complete", fake_complete)
    provider = client.post(
        "/admin/providers", headers=admin_headers, json={"name": "Mock", "base_url": "mock://local"}
    ).json()
    route = client.post(
        "/admin/routes",
        headers=admin_headers,
        json={
            "name": "mock-route",
            "model_name": "admin-selected",
            "upstream_model": "mock-model",
            "model_names": ["public-alias"],
            "mode": "llm",
            "provider_id": provider["id"],
        },
    ).json()
    sync = client.post(f"/admin/providers/{provider['id']}/models/sync", headers=admin_headers)
    assert sync.status_code == 200
    assert sync.json()["data"][0]["id"] == "mock-model"
    created = create_key(client, admin_headers, route_id=route["id"], model_name="ignored-by-route")
    assert created["secret"].startswith("hlg_")
    assert created["route_mode"] == "llm"
    assert client.get(
        "/v1/models", headers={"Authorization": f"Bearer {created['secret']}"}
    ).json()["data"] == [
        {"id": "admin-selected", "object": "model", "owned_by": "human-llm-gateway"},
        {"id": "public-alias", "object": "model", "owned_by": "human-llm-gateway"},
    ]
    assert (
        client.get("/admin/api-keys", headers=admin_headers).json()[0]["prefix"]
        != created["secret"]
    )

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created['secret']}"},
        json={"model": "some-client-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "admin-selected"
    assert called["model"] == "mock-model"
    assert response.json()["choices"][0]["message"]["content"] == "admin selected"


def test_web_reply_uses_same_dsl_and_records_web_source(client, admin_headers):
    created = create_key(client, admin_headers)
    with database.SessionLocal() as db:
        task = RequestTask(
            api_key_id=created["id"],
            protocol="openai",
            model="human-default",
            request_json="{}",
            status=TaskStatus.HUMAN_WAITING,
        )
        db.add(task)
        db.commit()
        task_id = task.id
    response = client.post(
        f"/admin/tasks/{task_id}/reply",
        headers=admin_headers,
        json={
            "text": "/think\n从网页处理\n/reply\n已完成\n/done",
        },
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/admin/tasks/{task_id}", headers=admin_headers).json()
    assert detail["status"] == "pseudo_streaming"
    assert [event["source"] for event in detail["events"]] == ["web", "web"]


def test_anthropic_mock_json_and_sse_end(client, admin_headers):
    provider = client.post(
        "/admin/providers",
        headers=admin_headers,
        json={"name": "Mock Anthropic", "base_url": "mock://anthropic"},
    ).json()
    route = client.post(
        "/admin/routes",
        headers=admin_headers,
        json={
            "name": "anthropic-route",
            "model_name": "claude-human",
            "mode": "llm",
            "provider_id": provider["id"],
        },
    ).json()
    created = create_key(
        client,
        admin_headers,
        name="anthropic-key",
        operator_name="Bob",
        im_name="Fake 2",
        route_id=route["id"],
        model_name="claude-human",
    )
    auth = {"Authorization": f"Bearer {created['secret']}"}
    response = client.post(
        "/v1/messages",
        headers=auth,
        json={
            "model": "claude-human",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["content"][0]["type"] == "text"
    stream = client.post(
        "/v1/messages",
        headers=auth,
        json={
            "model": "claude-human",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert stream.status_code == 200
    assert "event: message_stop" in stream.text


@pytest.mark.asyncio
async def test_personal_wechat_sidecar_never_reports_fake_success():
    connector = WeChatSidecarConnector()
    assert (await connector.health())["implemented"] is False
    with pytest.raises(RuntimeError):
        await connector.send_task(None)  # type: ignore[arg-type]

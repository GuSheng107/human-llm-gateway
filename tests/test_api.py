import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.db as database
from app.api import create_app
from app.config import get_settings
from app.dsl import ParsedEvent
from app.enums import EventKind, TaskStatus
from app.llm import LLMAdapter
from app.model_catalog import default_public_models
from app.models import AdminUser, RequestTask
from app.security import verify_password


def create_bot(client, user_headers, *, name="API Bot"):
    response = client.post(
        "/api/im-connections",
        headers=user_headers,
        json={"name": name, "platform": "webhook", "config": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_key(client, admin_headers, connection_id, **overrides):
    payload = {
        "name": "human-key",
        "operator_name": "Alice",
        "im_connection_id": connection_id,
        "route_name": "human-route",
        "model_name": "human-default",
        **overrides,
    }
    response = client.post("/admin/api-keys", headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def parse_sse(text):
    events = []
    event_name = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: ") and line != "data: [DONE]":
            body = json.loads(line.removeprefix("data: "))
            events.append((event_name, body))
            event_name = None
    return events


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
                "/auth/login",
                json={"username": "bootstrap-admin", "password": "bootstrap-password"},
            )
            assert response.status_code == 200
            assert response.json()["role"] == "admin"

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


def test_admin_configures_provider_route_and_key_binding(
    client, admin_headers, user_headers, monkeypatch
):
    called = {}

    async def complete(self, provider, model, messages, app_secret):
        called["model"] = model
        return [ParsedEvent(EventKind.FINAL, "admin selected")]

    monkeypatch.setattr(LLMAdapter, "complete", complete)
    bot = create_bot(client, user_headers)
    provider = client.post(
        "/admin/providers",
        headers=admin_headers,
        json={"name": "Mock", "base_url": "mock://local"},
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
    sync = client.post(
        f"/admin/providers/{provider['id']}/models/sync", headers=admin_headers
    )
    assert sync.status_code == 200
    assert sync.json()["data"][0]["id"] == "mock-model"
    created = create_key(
        client,
        admin_headers,
        bot["id"],
        route_id=route["id"],
        model_name="ignored-by-route",
    )
    assert created["secret"].startswith("hlg_")
    assert created["route_mode"] == "llm"
    auth = {"Authorization": f"Bearer {created['secret']}"}
    assert client.get("/v1/models", headers=auth).json()["data"] == default_public_models()
    assert client.get("/admin/api-keys", headers=admin_headers).json()[0]["prefix"] != created["secret"]

    response = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "some-client-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "admin-selected"
    assert called["model"] == "mock-model"
    assert response.json()["choices"][0]["message"]["content"] == "admin selected"


def test_api_key_requires_existing_user_owned_bot(client, admin_headers):
    response = client.post(
        "/admin/api-keys",
        headers=admin_headers,
        json={
            "name": "invalid-key",
            "operator_name": "Nobody",
            "im_connection_id": 9999,
        },
    )
    assert response.status_code == 404


def test_web_reply_uses_same_dsl_and_records_web_source(
    client, admin_headers, user_headers
):
    bot = create_bot(client, user_headers)
    created = create_key(client, admin_headers, bot["id"])
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
        json={"text": "/think\n从网页处理\n/reply\n已完成\n/done"},
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/admin/tasks/{task_id}", headers=admin_headers).json()
    assert detail["status"] == "pseudo_streaming"
    assert [event["source"] for event in detail["events"]] == ["web", "web"]


def test_admin_manages_public_model_catalog(client, admin_headers, user_headers):
    listed = client.get("/admin/models", headers=admin_headers)
    assert listed.status_code == 200
    seeded = listed.json()
    assert len(seeded) == 34
    assert seeded[0]["model_id"] == "deepseek-v4-pro"

    # 非管理员无权访问
    assert client.get("/admin/models", headers=user_headers).status_code == 403

    created = client.post(
        "/admin/models",
        headers=admin_headers,
        json={"model_id": "custom-model-1", "owned_by": "custom", "sort_order": -1},
    )
    assert created.status_code == 200, created.text
    entry = created.json()
    assert entry["active"] is True

    duplicate = client.post(
        "/admin/models", headers=admin_headers, json={"model_id": "custom-model-1"}
    )
    assert duplicate.status_code == 409
    assert client.post(
        "/admin/models", headers=admin_headers, json={"model_id": "has space"}
    ).status_code == 422

    updated = client.put(
        f"/admin/models/{entry['id']}",
        headers=admin_headers,
        json={"model_id": "custom-model-2", "active": False, "sort_order": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["model_id"] == "custom-model-2"
    assert updated.json()["active"] is False
    assert updated.json()["sort_order"] == 5

    # 改名冲突走 409
    conflict = client.put(
        f"/admin/models/{entry['id']}",
        headers=admin_headers,
        json={"model_id": "deepseek-v4-pro"},
    )
    assert conflict.status_code == 409
    assert client.put(
        f"/admin/models/{entry['id']}", headers=user_headers, json={"active": True}
    ).status_code == 403
    assert client.get("/admin/models/99999", headers=admin_headers).status_code in (405, 404)
    assert client.delete(
        f"/admin/models/{entry['id']}", headers=user_headers
    ).status_code == 403

    deleted = client.delete(f"/admin/models/{entry['id']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.delete(f"/admin/models/{entry['id']}", headers=admin_headers).status_code == 404

    audits = client.get(
        "/admin/audit-logs", headers=admin_headers, params={"action": "public_model.deleted"}
    ).json()
    assert audits and audits[0]["subject_id"] == str(entry["id"])


def test_v1_models_reflects_runtime_admin_configuration_and_no_reseed(
    client, admin_headers, user_headers
):
    bot = client.post(
        "/api/im-connections",
        headers=user_headers,
        json={"name": "Catalog Bot", "platform": "webhook", "config": {}},
    ).json()
    key = create_key(client, admin_headers, bot["id"])
    auth = {"Authorization": f"Bearer {key['secret']}"}

    assert len(client.get("/v1/models", headers=auth).json()["data"]) == 34

    # 停用全部模型后，/v1/models 为空；重启（重入 lifespan）不会补回默认模型
    for item in client.get("/admin/models", headers=admin_headers).json():
        response = client.delete(f"/admin/models/{item['id']}", headers=admin_headers)
        assert response.status_code == 200
    assert client.get("/v1/models", headers=auth).json()["data"] == []

    with TestClient(create_app()), database.SessionLocal() as db:
        from app.model_catalog import seed_public_models

        assert seed_public_models(db) == 0
    assert client.get("/v1/models", headers=auth).json()["data"] == []


def test_openai_and_anthropic_json_and_stream_contracts(
    client, admin_headers, user_headers, monkeypatch
):
    async def complete(self, provider, model, messages, app_secret):
        return [
            ParsedEvent(EventKind.REASONING, "先分析"),
            ParsedEvent(EventKind.TOOL_CALL, "", "lookup", '{"id":1}'),
            ParsedEvent(EventKind.FINAL, "最终答案"),
        ]

    monkeypatch.setattr(LLMAdapter, "complete", complete)
    settings = get_settings()
    monkeypatch.setattr(settings, "stream_delay_min_ms", 0)
    monkeypatch.setattr(settings, "stream_delay_max_ms", 0)
    monkeypatch.setattr(settings, "stream_chunk_size", 3)

    bot = create_bot(client, user_headers, name="Protocol Bot")
    provider = client.post(
        "/admin/providers",
        headers=admin_headers,
        json={"name": "Protocol Mock", "base_url": "mock://protocol"},
    ).json()
    route = client.post(
        "/admin/routes",
        headers=admin_headers,
        json={
            "name": "protocol-route",
            "model_name": "human-protocol",
            "mode": "llm",
            "provider_id": provider["id"],
        },
    ).json()
    key = create_key(
        client,
        admin_headers,
        bot["id"],
        route_id=route["id"],
        model_name="human-protocol",
    )
    auth = {"Authorization": f"Bearer {key['secret']}"}

    chat = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "client-alias", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 200
    chat_message = chat.json()["choices"][0]["message"]
    assert chat_message["reasoning_content"] == "先分析"
    assert chat_message["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": '{"id":1}',
    }
    assert chat_message["content"] == "最终答案"

    chat_stream = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={
            "model": "client-alias",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert chat_stream.status_code == 200
    assert "data: [DONE]" in chat_stream.text
    assert '"reasoning_content"' in chat_stream.text
    assert '"tool_calls"' in chat_stream.text

    anthropic = client.post(
        "/v1/messages",
        headers=auth,
        json={
            "model": "client-alias",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert anthropic.status_code == 200
    assert [block["type"] for block in anthropic.json()["content"]] == [
        "thinking",
        "tool_use",
        "text",
    ]
    assert anthropic.json()["content"][0]["signature"]

    anthropic_stream = client.post(
        "/v1/messages",
        headers=auth,
        json={
            "model": "client-alias",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    anthropic_events = parse_sse(anthropic_stream.text)
    event_names = [name for name, _ in anthropic_events]
    assert event_names[0] == "message_start"
    assert event_names[-1] == "message_stop"
    assert event_names.count("content_block_start") == 3
    assert event_names.count("content_block_stop") == 3
    assert any(
        body.get("delta", {}).get("type") == "signature_delta"
        for _, body in anthropic_events
    )
    assert anthropic_events[0][1]["message"]["stop_reason"] is None

    response = client.post(
        "/v1/responses",
        headers=auth,
        json={"model": "client-alias", "input": "hi"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["object"] == "response"
    assert response.json()["reasoning"]["summary"] is None
    assert [item["type"] for item in response.json()["output"]] == [
        "reasoning",
        "function_call",
        "message",
    ]

    response_stream = client.post(
        "/v1/responses",
        headers=auth,
        json={"model": "client-alias", "input": "hi", "stream": True},
    )
    response_events = parse_sse(response_stream.text)
    response_names = [name for name, _ in response_events]
    assert response_names[:2] == ["response.created", "response.in_progress"]
    assert response_names[-1] == "response.completed"
    assert "response.function_call_arguments.delta" in response_names
    sequences = [body["sequence_number"] for _, body in response_events]
    assert sequences == list(range(len(sequences)))

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.db as database
from app.api import create_app
from app.config import get_settings
from app.dsl import ParsedEvent
from app.enums import EventKind
from app.llm import LLMAdapter
from app.models import AdminUser
from app.security import verify_password
from app.services import TaskError, TaskService


def create_bot(client, user_headers, *, name="API Bot"):
    response = client.post(
        "/api/im-connections",
        headers=user_headers,
        json={"name": name, "platform": "webhook", "config": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_catalog_entry(client, admin_headers, model_id="custom-alias", **overrides):
    payload = {"model_id": model_id, "owned_by": "custom", **overrides}
    response = client.post("/api/model-catalog", headers=admin_headers, json=payload)
    if response.status_code == 409:
        listed = client.get("/api/model-catalog", headers=admin_headers).json()
        for item in listed:
            if item["model_id"] == model_id:
                return item
    assert response.status_code == 200, response.text
    return response.json()


def create_provider(client, user_headers, *, name="Mock", base_url="mock://local", **overrides):
    payload = {"name": name, "base_url": base_url, "protocol": "openai_compatible", **overrides}
    response = client.post("/api/providers", headers=user_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def create_route(
    client,
    user_headers,
    *,
    name="human-route",
    model_name="custom-alias",
    mode="human",
    **overrides,
):
    payload = {"name": name, "model_name": model_name, "mode": mode, **overrides}
    response = client.post("/api/model-routes", headers=user_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def create_key(client, user_headers, route_id, **overrides):
    payload = {"name": "human-key", "route_id": route_id, **overrides}
    response = client.post("/api/api-keys", headers=user_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def bind_connection(client, user_headers, connection_id):
    """走完整绑定流程：生成绑定码 → 模拟 IM 发送 /bind。"""
    import asyncio

    from app.connectors.base import InboundMessage
    from app.inbound import InboundProcessor

    started = client.post(f"/api/im-connections/{connection_id}/binding", headers=user_headers)
    assert started.status_code == 200, started.text
    code = started.json()["code"]
    with database.SessionLocal() as db:
        processor = InboundProcessor(get_settings(), None)
        message = InboundMessage(
            connector_id=connection_id,
            sender_id="owner-im-id",
            text=f"/bind {code}",
            conversation_id="conv-1",
            external_message_id=None,
        )
        asyncio.run(processor.handle(db, message))
    status = client.get(
        f"/api/im-connections/{connection_id}/binding/status", headers=user_headers
    ).json()
    assert status["binding_status"] == "bound", status


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
                "/api/auth/login",
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


def test_unified_error_structure_and_request_id(client):
    response = client.post("/api/im-connections")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth_expired"
    assert body["error"]["action"] == "relogin"
    assert body["error"]["request_id"]
    assert response.headers.get("X-Request-Id") == body["error"]["request_id"]


def test_user_self_service_provider_route_key_flow(
    client, admin_headers, user_headers, monkeypatch
):
    called = {}

    async def complete(self, provider, model, messages, app_secret):
        called["model"] = model
        return [ParsedEvent(EventKind.FINAL, "user selected")]

    monkeypatch.setattr(LLMAdapter, "complete", complete)

    entry = create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    assert entry["model_id"] == "deepseek-v4-pro"

    provider = create_provider(client, user_headers)
    sync = client.post(f"/api/providers/{provider['id']}/models/sync", headers=user_headers)
    assert sync.status_code == 200
    assert sync.json()["data"][0]["id"] == "mock-model"

    route = create_route(
        client,
        user_headers,
        name="llm-route",
        model_name="deepseek-v4-pro",
        mode="llm",
        provider_id=provider["id"],
        upstream_model="mock-model",
    )
    assert route["model_name"] == "deepseek-v4-pro"
    assert route["upstream_model"] == "mock-model"

    bot = create_bot(client, user_headers)
    bind_connection(client, user_headers, bot["id"])
    created = create_key(client, user_headers, route["id"], im_connection_id=bot["id"])
    assert created["secret"].startswith("hlg_")
    assert created["binding_type"] == "im"
    auth = {"Authorization": f"Bearer {created['secret']}"}

    response = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "any-fake-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert called["model"] == "mock-model"
    assert response.json()["choices"][0]["message"]["content"] == "user selected"

    keys = client.get("/api/api-keys", headers=user_headers).json()
    assert keys["items"][0]["prefix"] != created["secret"]


def test_web_only_key_waits_for_web_reply(client, admin_headers, user_headers):
    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    route = create_route(client, user_headers, model_name="deepseek-v4-pro", mode="human")
    created = create_key(client, user_headers, route["id"])
    assert created["binding_type"] == "web"
    assert created["im_connection_id"] is None

    auth = {"Authorization": f"Bearer {created['secret']}"}
    import threading

    result = {}

    def call_inference():
        result["response"] = client.post(
            "/v1/chat/completions",
            headers=auth,
            json={"model": "any-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    thread = threading.Thread(target=call_inference)
    thread.start()

    task_id = None
    for _ in range(50):
        tasks = client.get("/api/tasks", headers=user_headers).json()["items"]
        waiting = [t for t in tasks if t["status"] == "human_waiting"]
        if waiting:
            task_id = waiting[0]["id"]
            break
        import time

        time.sleep(0.1)
    assert task_id, "纯 Web Key 的任务应进入 human_waiting"

    admin_reply = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=admin_headers,
        json={"text": "/reply\n管理员代答\n/done"},
    )
    assert admin_reply.status_code == 403

    reply = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=user_headers,
        json={"text": "/think\nweb 处理\n/reply\n已完成\n/done"},
    )
    assert reply.status_code == 200, reply.text
    thread.join(timeout=10)
    assert result["response"].status_code == 200
    assert result["response"].json()["choices"][0]["message"]["content"] == "已完成"

    detail = client.get(f"/api/tasks/{task_id}", headers=user_headers).json()
    assert [e["source"] for e in detail["events"]] == ["web", "web"]


def test_multiple_api_keys_can_share_one_im_connection(client, admin_headers, user_headers):
    create_catalog_entry(client, admin_headers, model_id="shared-im-model")
    first_route = create_route(
        client, user_headers, name="first-route", model_name="shared-im-model", mode="human"
    )
    second_route = create_route(
        client, user_headers, name="second-route", model_name="shared-im-model", mode="human"
    )
    bot = create_bot(client, user_headers, name="Shared Bot")
    bind_connection(client, user_headers, bot["id"])

    first = create_key(client, user_headers, first_route["id"], im_connection_id=bot["id"])
    second = create_key(client, user_headers, second_route["id"], im_connection_id=bot["id"])

    assert first["im_connection_id"] == bot["id"]
    assert second["im_connection_id"] == bot["id"]


def test_inference_failure_does_not_expose_human_workflow(
    client, admin_headers, user_headers, monkeypatch
):
    create_catalog_entry(client, admin_headers, model_id="private-workflow-model")
    route = create_route(
        client,
        user_headers,
        model_name="private-workflow-model",
        mode="human",
        human_timeout_seconds=10,
    )
    key = create_key(client, user_headers, route["id"])

    async def fail_without_waiting(self, task_id, timeout):
        raise TaskError("人工回复超时")

    monkeypatch.setattr(TaskService, "await_human", fail_without_waiting)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['secret']}"},
        json={
            "model": "private-workflow-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 500
    serialized = json.dumps(response.json(), ensure_ascii=False).lower()
    assert all(marker not in serialized for marker in ("人工", "human", "fallback", "im"))


def test_route_model_name_must_come_from_catalog(client, admin_headers, user_headers):
    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    response = client.post(
        "/api/model-routes",
        headers=user_headers,
        json={"name": "bad", "model_name": "not-in-catalog", "mode": "human"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_route_upstream_model_must_be_synced(client, admin_headers, user_headers):
    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    provider = create_provider(client, user_headers)
    response = client.post(
        "/api/model-routes",
        headers=user_headers,
        json={
            "name": "unsynced",
            "model_name": "deepseek-v4-pro",
            "mode": "llm",
            "provider_id": provider["id"],
            "upstream_model": "never-synced",
        },
    )
    assert response.status_code == 422
    assert "同步" in response.json()["error"]["message"]


def test_ownership_isolation_returns_403(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers)
    other_admin = admin_headers

    patch = client.post(
        f"/api/im-connections/{bot['id']}/update",
        headers=other_admin,
        json={"config": {}},
    )
    assert patch.status_code == 403

    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    route = create_route(client, user_headers, model_name="deepseek-v4-pro")
    stolen = client.post(
        "/api/api-keys",
        headers=other_admin,
        json={"name": "steal", "route_id": route["id"]},
    )
    assert stolen.status_code == 403


def test_admin_cannot_read_user_login_snapshot(client, admin_headers, user_headers):
    bot = create_bot(client, user_headers)
    response = client.get(f"/api/im-connections/{bot['id']}/login", headers=admin_headers)
    assert response.status_code == 403


def test_connection_update_marks_pending_restart_and_apply(client, user_headers):
    bot = create_bot(client, user_headers, name="Webhook Bot")
    updated = client.post(
        f"/api/im-connections/{bot['id']}/update",
        headers=user_headers,
        json={"name": "Webhook Bot v2", "config": {"target_url": "https://example.com/hook"}},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "Webhook Bot v2"
    assert body["restart_required"] is True
    assert body["status"] == "pending_restart"
    assert body["config_version"] == 2
    assert "apply" in body["allowed_actions"]

    applied = client.post(f"/api/im-connections/{bot['id']}/apply", headers=user_headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["restart_required"] is False
    assert applied.json()["applied_version"] == 2


def test_connection_update_keeps_secret_when_blank(client, user_headers):
    bot = create_bot(client, user_headers, name="Wecom Bot")
    created = client.post(
        f"/api/im-connections/{bot['id']}/update",
        headers=user_headers,
        json={"config": {"bot_id": "bot-1", "secret": "real-secret"}},
    )
    assert created.status_code == 200, created.text

    patched = client.post(
        f"/api/im-connections/{bot['id']}/update",
        headers=user_headers,
        json={"config": {"bot_id": "bot-2", "secret": ""}},
    )
    assert patched.status_code == 200, patched.text

    from app.connection_config import load_connection_config
    from app.models import IMConnection

    with database.SessionLocal() as db:
        conn = db.get(IMConnection, bot["id"])
        config = load_connection_config(conn.config_json)
        assert config["secret"] == "real-secret"
        assert config["bot_id"] == "bot-2"


def test_binding_lockout_after_repeated_failures(client, user_headers):
    bot = create_bot(client, user_headers, name="Bind Bot")
    start = client.post(f"/api/im-connections/{bot['id']}/binding", headers=user_headers)
    assert start.status_code == 200

    from app.connectors.base import InboundMessage
    from app.inbound import InboundProcessor

    with database.SessionLocal() as db:
        processor = InboundProcessor(get_settings(), None)
        for _ in range(5):
            import asyncio

            wrong = InboundMessage(
                connector_id=bot["id"],
                sender_id="someone",
                text="/bind WRONGCODE",
                conversation_id="c1",
                external_message_id=None,
            )
            asyncio.run(processor.handle(db, wrong))

    status = client.get(
        f"/api/im-connections/{bot['id']}/binding/status", headers=user_headers
    ).json()
    assert status["binding_status"] == "locked"
    assert status["locked"] is True
    assert status["failed_attempts"] >= 5

    locked = client.post(f"/api/im-connections/{bot['id']}/binding", headers=user_headers)
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "binding_locked"


def test_admin_manages_public_model_catalog(client, admin_headers, user_headers):
    listed = client.get("/api/model-catalog", headers=admin_headers)
    assert listed.status_code == 200
    seeded = listed.json()
    assert len(seeded) == 34
    assert seeded[0]["model_id"] == "deepseek-v4-pro"

    assert client.get("/api/model-catalog", headers=user_headers).status_code == 200
    assert (
        client.post(
            "/api/model-catalog",
            headers=user_headers,
            json={"model_id": "user-try"},
        ).status_code
        == 403
    )

    created = client.post(
        "/api/model-catalog",
        headers=admin_headers,
        json={"model_id": "custom-model-1", "owned_by": "custom", "sort_order": -1},
    )
    assert created.status_code == 200, created.text
    entry = created.json()

    duplicate = client.post(
        "/api/model-catalog", headers=admin_headers, json={"model_id": "custom-model-1"}
    )
    assert duplicate.status_code == 409

    updated = client.post(
        f"/api/model-catalog/{entry['id']}/update",
        headers=admin_headers,
        json={"model_id": "custom-model-2", "active": False, "sort_order": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["model_id"] == "custom-model-2"
    assert updated.json()["active"] is False

    deleted = client.post(f"/api/model-catalog/{entry['id']}/delete", headers=admin_headers)
    assert deleted.status_code == 200
    assert (
        client.post(f"/api/model-catalog/{entry['id']}/delete", headers=admin_headers).status_code
        == 404
    )

    audits = client.get(
        "/api/audit-logs", headers=admin_headers, params={"action": "public_model.deleted"}
    ).json()
    assert audits["items"] and audits["items"][0]["subject_id"] == str(entry["id"])


def test_v1_models_reflects_runtime_admin_configuration_and_no_reseed(
    client, admin_headers, user_headers
):
    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    route = create_route(client, user_headers, model_name="deepseek-v4-pro")
    key = create_key(client, user_headers, route["id"])
    auth = {"Authorization": f"Bearer {key['secret']}"}

    assert len(client.get("/v1/models", headers=auth).json()["data"]) == 34

    for item in client.get("/api/model-catalog", headers=admin_headers).json():
        response = client.post(f"/api/model-catalog/{item['id']}/delete", headers=admin_headers)
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

    create_catalog_entry(client, admin_headers, model_id="deepseek-v4-pro")
    provider = create_provider(
        client, user_headers, name="Protocol Mock", base_url="mock://protocol"
    )
    client.post(f"/api/providers/{provider['id']}/models/sync", headers=user_headers)
    route = create_route(
        client,
        user_headers,
        name="protocol-route",
        model_name="deepseek-v4-pro",
        mode="llm",
        provider_id=provider["id"],
        upstream_model="mock-model",
    )
    key = create_key(client, user_headers, route["id"])
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

    response = client.post(
        "/v1/responses",
        headers=auth,
        json={"model": "client-alias", "input": "hi"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["object"] == "response"

    response_stream = client.post(
        "/v1/responses",
        headers=auth,
        json={"model": "client-alias", "input": "hi", "stream": True},
    )
    response_events = parse_sse(response_stream.text)
    response_names = [name for name, _ in response_events]
    assert response_names[:2] == ["response.created", "response.in_progress"]
    assert response_names[-1] == "response.completed"


def test_settings_read_and_update(client, admin_headers):
    listed = client.get("/api/settings", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["items"]["binding_max_attempts"] == 5

    updated = client.post("/api/settings", headers=admin_headers, json={"binding_max_attempts": 3})
    assert updated.status_code == 200
    assert updated.json()["items"]["binding_max_attempts"] == 3

    invalid = client.post("/api/settings", headers=admin_headers, json={"app_secret": "hacked"})
    assert invalid.status_code == 422

    forbidden = client.get("/api/settings", headers=None)
    assert forbidden.status_code == 401


def test_logs_filtering_and_pagination(client, admin_headers):
    response = client.get(
        "/api/audit-logs", headers=admin_headers, params={"page": 1, "page_size": 5}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 5
    assert len(body["items"]) <= 5

    app_logs = client.get("/api/app-logs", headers=admin_headers, params={"level": "info"})
    assert app_logs.status_code == 200
    assert all(item["level"] == "info" for item in app_logs.json()["items"])

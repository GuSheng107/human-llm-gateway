"""M5 API Key、/v1/models 与并发准入测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

import app.core.db as database
from app.domain.enums import (
    DeliveryMode,
    InferenceProtocol,
    ReplyStrategy,
    TaskState,
)
from app.domain.errors import DomainError, DomainErrorCode
from app.repositories.models import ApiKey, LlmConfig, RequestTask, User
from app.services.admission import AdmissionService
from app.services.effective_models import EffectiveModelService

_TIMEOUT_MESSAGE = "人工超时时间必须在 10 到 1800 秒之间"


def _login(client, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_token": "test-token",
            "captcha_code": "test",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_user(client, admin_headers, username: str, password: str = "User-Pass1!") -> dict:
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": username, "display_name": username, "password": password},
    )
    assert created.status_code == 201, created.text
    headers = _login(client, username, password)
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": password, "new_password": "Changed-Pass2!"},
    )
    assert changed.status_code == 200, changed.text
    return headers


def _create_api_key(client, headers, **payload) -> tuple[dict, str]:
    body = {"name": payload.pop("name", "默认 Key"), **payload}
    response = client.post("/api/api-keys", headers=headers, json=body)
    return response, response.json() if response.status_code == 201 else {}


def test_api_key_plaintext_only_in_creation_response(client, admin_headers) -> None:
    """所有者和管理员的后续响应都不能取回完整 Key。"""
    headers = _create_user(client, admin_headers, "key-user")
    response, body = _create_api_key(client, headers, name="完整明文")
    assert response.status_code == 201, response.text
    plaintext = body["plaintext"]
    assert plaintext.startswith("sk-")
    assert len(plaintext) == 46

    # Owner 本人：离开创建场景后也只能看到前缀。
    listed = client.get("/api/api-keys", headers=headers).json()
    item = listed["items"][0]
    assert "plaintext" not in item
    assert item["key_prefix"] == plaintext[:8]
    assert len(item["key_prefix"]) == 8
    assert "key" not in item
    assert plaintext not in response_text(listed)
    updated = client.patch(
        f"/api/api-keys/{item['id']}", headers=headers, json={"name": "完整明文"}
    )
    assert updated.status_code == 200
    assert plaintext not in updated.text
    with database.SessionLocal() as session:
        assert session.get(ApiKey, int(item["id"])).key_ciphertext is None

    # Admin 监管：能看到该 Key 记录，但不返回完整明文。
    admin_listed = client.get("/api/api-keys", headers=admin_headers).json()
    admin_items = [i for i in admin_listed["items"] if i["name"] == "完整明文"]
    assert admin_items, "admin 应能看到所有用户的 Key"
    assert "key" not in admin_items[0]
    assert admin_items[0]["key_prefix"] == plaintext[:8]


def response_text(payload) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def test_delivery_mode_and_strategy_validation(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-2")
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])

    # IM 入口必须选择自己的连接。
    bad_im = client.post(
        "/api/api-keys", headers=headers, json={"name": "bad-im", "delivery_mode": "im"}
    )
    assert bad_im.status_code == 400

    connection_id = _connection_id(user_id)
    im_key = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "im-key", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    assert im_key.status_code == 201, im_key.text
    assert im_key.json()["delivery_mode"] == "im"

    # Web 入口不能绑定连接。
    web_with_conn = client.post(
        "/api/api-keys",
        headers=headers,
        json={
            "name": "web-key",
            "delivery_mode": "web",
            "im_connection_id": connection_id,
        },
    )
    assert web_with_conn.status_code == 400

    # llm 策略必须选择自己的 LLM 配置。
    bad_llm = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "bad-llm", "reply_strategy": "llm"},
    )
    assert bad_llm.status_code == 400

    llm_config_id = _llm_config_id(user_id)
    llm_key = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "llm-key", "reply_strategy": "llm", "llm_config_id": llm_config_id},
    )
    assert llm_key.status_code == 201, llm_key.text
    assert llm_key.json()["reply_strategy"] == "llm"

    # 人工策略不能带 LLM 配置。
    human_with_llm = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "human-key", "reply_strategy": "human", "llm_config_id": llm_config_id},
    )
    assert human_with_llm.status_code == 400

    # 已停用的 LLM 配置不能绑定到 Key。
    disabled_config_id = _llm_config_id(user_id, enabled=False)
    disabled_binding = client.post(
        "/api/api-keys",
        headers=headers,
        json={
            "name": "disabled-llm-key",
            "reply_strategy": "llm",
            "llm_config_id": disabled_config_id,
        },
    )
    assert disabled_binding.status_code == 400

    # 别人的连接/配置不能引用。
    other_headers = _create_user(client, admin_headers, "key-user-3")
    other_id = int(client.get("/api/auth/me", headers=other_headers).json()["id"])
    foreign = client.post(
        "/api/api-keys",
        headers=other_headers,
        json={"name": "foreign", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    assert foreign.status_code == 400
    assert other_id != user_id


def test_human_timeout_range_is_enforced(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-4")
    too_small = client.post(
        "/api/api-keys", headers=headers, json={"name": "t1", "human_timeout_seconds": 9}
    )
    assert too_small.status_code == 422
    too_big = client.post(
        "/api/api-keys", headers=headers, json={"name": "t2", "human_timeout_seconds": 1801}
    )
    assert too_big.status_code == 422
    ok = client.post(
        "/api/api-keys", headers=headers, json={"name": "t3", "human_timeout_seconds": 900}
    )
    assert ok.status_code == 201
    assert ok.json()["human_timeout_seconds"] == 900


def test_disabled_or_deleted_key_blocks_new_requests_but_admitted_task_continues(
    client, admin_headers
) -> None:
    headers = _create_user(client, admin_headers, "key-user-5")
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    created = client.post("/api/api-keys", headers=headers, json={"name": "lifecycle"}).json()
    key_id = int(created["id"])
    plaintext = created["plaintext"]

    # 启用状态下可正常调用 /v1/models。
    assert (
        client.get("/v1/models", headers={"Authorization": f"Bearer {plaintext}"}).status_code
        == 200
    )

    # 已准入任务：占用名额并处于等待人工状态。
    with database.SessionLocal() as session:
        task = RequestTask(
            public_id="task_lifecycle",
            owner_user_id=user_id,
            api_key_id=key_id,
            api_key_prefix_snapshot=created["key_prefix"],
            api_key_name_snapshot=created["name"],
            requested_model="deepseek-v4-pro",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json="{}",
            normalized_request_json="{}",
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.WEB,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=_now(),
        )
        session.add(task)
        session.commit()
        task_id = task.id

    disabled = client.patch(f"/api/api-keys/{key_id}", headers=headers, json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["is_enabled"] is False

    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        user = session.get(User, user_id)
        with pytest.raises(DomainError) as captured:
            AdmissionService().acquire_slot(session, key, user)
        assert captured.value.code is DomainErrorCode.INVALID_API_KEY
        # 已准入任务不受影响。
        assert session.get(RequestTask, task_id).state is TaskState.WAITING_HUMAN
        session.rollback()

    assert client.delete(f"/api/api-keys/{key_id}", headers=headers).status_code == 204
    assert client.get(f"/api/api-keys/{key_id}", headers=headers).status_code == 404


def _now():
    from app.core.time import utc_now

    return utc_now()


def _connection_id(owner_user_id: int) -> int:
    from app.services.connection_service import ConnectionService

    with database.SessionLocal() as session:
        service = ConnectionService()
        row, _generated = service.create(
            session,
            owner=session.get(User, owner_user_id),
            name="key-entry-conn",
            platform="webhook",
            config={"outbound_url": "https://example.test/hook"},
        )
        session.commit()
        return row.id


def _llm_config_id(owner_user_id: int, *, enabled: bool = True) -> int:
    from app.core.config import get_settings
    from app.core.security import encrypt_secret

    with database.SessionLocal() as session:
        secret = encrypt_secret("sk-test", get_settings().app_secret, "llm-secret")
        config = LlmConfig(
            owner_user_id=owner_user_id,
            name=f"upstream-{owner_user_id}-{'on' if enabled else 'off'}",
            protocol="openai_chat",
            base_url="https://example.test/v1",
            real_model="gpt-test",
            secret_ciphertext=secret,
            encryption_key_version=1,
            timeout_seconds=60,
            is_enabled=enabled,
        )
        session.add(config)
        session.commit()
        return config.id


def test_v1_models_requires_api_key_auth(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-7")
    created = client.post("/api/api-keys", headers=headers, json={"name": "models"}).json()
    plaintext = created["plaintext"]

    anonymous = client.get("/v1/models")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "invalid_api_key"

    invalid = client.get("/v1/models", headers={"Authorization": "Bearer sk-not-a-real-key"})
    assert invalid.status_code == 401
    assert invalid.json()["error"]["type"] == "invalid_request_error"

    ok = client.get("/v1/models", headers={"Authorization": f"Bearer {plaintext}"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["object"] == "list"
    model_ids = {item["id"] for item in body["data"]}
    assert {"deepseek-v4-pro", "deepseek-v4-flash"} <= model_ids
    for item in body["data"]:
        assert item["object"] == "model"
        assert item["owned_by"]
        assert isinstance(item["created"], int)


def test_v1_models_respects_group_and_key_selection_narrowing(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-8")
    listed = client.get("/api/fake-models", headers=headers).json()["items"]
    by_id = {item["model_id"]: int(item["id"]) for item in listed}
    platform_group = client.post(
        "/api/model-groups",
        headers=admin_headers,
        json={"name": "平台分组筛选"},
    ).json()
    assert platform_group["is_public"] is True
    assigned = client.patch(
        f"/api/fake-models/{by_id['deepseek-v4-pro']}",
        headers=admin_headers,
        json={"group_ids": [int(platform_group["id"])]},
    )
    assert assigned.status_code == 200, assigned.text
    private = client.post(
        "/api/fake-models",
        headers=headers,
        json={
            "model_id": "narrow-private",
            "group_ids": [int(platform_group["id"])],
        },
    ).json()
    by_id["narrow-private"] = int(private["id"])

    grouped_key = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "grouped", "model_group_id": int(platform_group["id"])},
    ).json()
    models = client.get(
        "/v1/models", headers={"Authorization": f"Bearer {grouped_key['plaintext']}"}
    ).json()
    assert {item["id"] for item in models["data"]} == {"deepseek-v4-pro", "narrow-private"}

    # Key 显式选择只能收窄，不能扩张出分组之外的模型。
    outside = client.post(
        "/api/api-keys",
        headers=headers,
        json={
            "name": "outside-group",
            "model_group_id": int(platform_group["id"]),
            "fake_model_ids": [by_id["deepseek-v4-flash"]],
        },
    )
    assert outside.status_code == 400

    selected = client.post(
        "/api/api-keys",
        headers=headers,
        json={
            "name": "selected",
            "model_group_id": int(platform_group["id"]),
            "fake_model_ids": [by_id["deepseek-v4-pro"]],
        },
    ).json()
    models = client.get(
        "/v1/models", headers={"Authorization": f"Bearer {selected['plaintext']}"}
    ).json()
    assert [item["id"] for item in models["data"]] == ["deepseek-v4-pro"]

    # 空集合 = 全部候选模型。
    cleared = client.patch(
        f"/api/api-keys/{selected['id']}",
        headers=headers,
        json={"fake_model_ids": []},
    )
    assert cleared.status_code == 200
    models = client.get(
        "/v1/models", headers={"Authorization": f"Bearer {selected['plaintext']}"}
    ).json()
    assert {item["id"] for item in models["data"]} == {"deepseek-v4-pro", "narrow-private"}


def test_admin_cannot_create_or_rewrite_keys_only_toggle_and_delete(client, admin_headers) -> None:
    """管理员监管：禁止新建；PATCH 仅允许启停；删除可用。"""
    user_headers = _create_user(client, admin_headers, "key-user-admin-rw")
    created = client.post(
        "/api/api-keys", headers=user_headers, json={"name": "admin-managed"}
    ).json()

    blocked = client.post("/api/api-keys", headers=admin_headers, json={"name": "admin-key"})
    assert blocked.status_code == 403
    assert "管理员" in blocked.json()["error"]["message"]

    # 管理员可以停用/启用普通用户的 Key。
    toggled = client.patch(
        f"/api/api-keys/{created['id']}", headers=admin_headers, json={"enabled": False}
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["is_enabled"] is False

    # 管理员不能改写 Key 的其他配置。
    rewritten = client.patch(
        f"/api/api-keys/{created['id']}",
        headers=admin_headers,
        json={"name": "renamed-by-admin"},
    )
    assert rewritten.status_code == 403

    deleted = client.delete(f"/api/api-keys/{created['id']}", headers=admin_headers)
    assert deleted.status_code == 204


def test_user_key_can_use_public_group(client, admin_headers) -> None:
    """用户新建 Key 可以选择管理员维护的公开分组（如供应商默认分组）。"""
    from app.repositories.models import ModelGroup

    headers = _create_user(client, admin_headers, "key-user-public-group")
    # 种子分组（管理员创建、is_public=True）。
    with database.SessionLocal() as session:
        public_group = session.scalars(
            select(ModelGroup).where(ModelGroup.is_public.is_(True))
        ).first()
        assert public_group is not None

    created = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "public-group-key", "model_group_id": int(public_group.id)},
    )
    assert created.status_code == 201, created.text
    models = client.get(
        "/v1/models", headers={"Authorization": f"Bearer {created.json()['plaintext']}"}
    ).json()
    assert len(models["data"]) > 0


def test_disabled_model_disappears_from_catalog_and_resolve(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-9")
    created = client.post("/api/api-keys", headers=headers, json={"name": "resolve"}).json()
    plaintext = created["plaintext"]

    listed = client.get("/api/fake-models", headers=headers).json()["items"]
    target = next(item for item in listed if item["model_id"] == "deepseek-v4-flash")

    with database.SessionLocal() as session:
        key = session.get(ApiKey, int(created["id"]))
        service = EffectiveModelService()
        assert service.resolve(session, key, "deepseek-v4-flash") is not None
        assert service.resolve(session, key, "does-not-exist") is None

    client.patch(f"/api/fake-models/{target['id']}", headers=admin_headers, json={"enabled": False})
    models = client.get("/v1/models", headers={"Authorization": f"Bearer {plaintext}"}).json()
    assert "deepseek-v4-flash" not in {item["id"] for item in models["data"]}

    with database.SessionLocal() as session:
        key = session.get(ApiKey, int(created["id"]))
        assert EffectiveModelService().resolve(session, key, "deepseek-v4-flash") is None


def test_key_selection_must_come_from_visible_candidates(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "key-user-10")
    other = _create_user(client, admin_headers, "key-user-11")
    foreign = client.post(
        "/api/fake-models", headers=other, json={"model_id": "foreign-model"}
    ).json()

    response = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "steal", "fake_model_ids": [int(foreign["id"])]},
    )
    assert response.status_code == 400
    assert "候选集" in response.json()["error"]["message"]


def test_deleted_model_or_group_keep_history_task_snapshot(client, admin_headers) -> None:
    """删除模型后活动任务仍按 requested_model 快照继续（docs/DATABASE.md §11）。"""
    headers = _create_user(client, admin_headers, "key-user-12")
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    created = client.post("/api/api-keys", headers=headers, json={"name": "snapshot"}).json()

    with database.SessionLocal() as session:
        task = RequestTask(
            public_id="task_snapshot",
            owner_user_id=user_id,
            api_key_id=int(created["id"]),
            api_key_prefix_snapshot=created["key_prefix"],
            api_key_name_snapshot=created["name"],
            requested_model="deepseek-v4-pro",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json="{}",
            normalized_request_json="{}",
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.WEB,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=_now(),
        )
        session.add(task)
        session.commit()

    listed = client.get("/api/fake-models", headers=headers).json()["items"]
    target = next(item for item in listed if item["model_id"] == "deepseek-v4-pro")
    assert (
        client.delete(f"/api/fake-models/{target['id']}", headers=admin_headers).status_code == 204
    )

    with database.SessionLocal() as session:
        task = session.query(RequestTask).filter(RequestTask.public_id == "task_snapshot").one()
        assert task.state is TaskState.WAITING_HUMAN
        assert task.requested_model == "deepseek-v4-pro"


def test_effective_model_query_is_shared_between_catalog_and_admission(
    client, admin_headers
) -> None:
    """/v1/models 与准入解析复用同一个查询，避免“看得到但调用不到”。"""
    headers = _create_user(client, admin_headers, "key-user-13")
    created = client.post("/api/api-keys", headers=headers, json={"name": "shared-query"}).json()
    plaintext = created["plaintext"]

    catalog = {
        item["id"]
        for item in client.get(
            "/v1/models", headers={"Authorization": f"Bearer {plaintext}"}
        ).json()["data"]
    }

    with database.SessionLocal() as session:
        key = session.get(ApiKey, int(created["id"]))
        service = EffectiveModelService()
        effective = {row.model_id for row in service.effective_models(session, key)}
        assert effective == catalog
        # 目录中的模型都能被解析，目录外的都不行。
        for model_id in effective:
            assert service.resolve(session, key, model_id) is not None
        assert service.resolve(session, key, "not-in-catalog") is None

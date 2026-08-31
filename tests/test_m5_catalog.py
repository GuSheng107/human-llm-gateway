"""M5 Fake Model 与模型分组测试：可见范围、遮蔽、分组筛选与治理权限。"""

from __future__ import annotations


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


def _model(client, headers, model_id: str, **extra) -> dict:
    payload = {"model_id": model_id, **extra}
    response = client.post("/api/fake-models", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_model_marketplace_metadata_and_filters(client, admin_headers) -> None:
    """模型广场：种子模型带价格/能力元数据；多维筛选与 include_disabled 生效。"""
    listed = client.get("/api/fake-models", headers=admin_headers).json()
    seeded = {item["model_id"]: item for item in listed["items"]}
    assert "deepseek-v4-pro" in seeded
    pro = seeded["deepseek-v4-pro"]
    assert pro["input_price_per_million"] == 4.0
    assert pro["output_price_per_million"] == 16.0
    assert pro["cached_input_price_per_million"] == 0.4
    assert pro["context_window"] == 128_000
    assert "tools" in pro["capabilities"]
    assert pro["billing_tier"] == "pay_as_you_go"
    assert pro["endpoint_type"] == "openai_chat"
    claude = seeded["claude-fable-5"]
    assert claude["endpoint_type"] == "anthropic_messages"

    # 端点筛选。
    anthropic_only = client.get(
        "/api/fake-models", headers=admin_headers, params={"endpoint_type": "anthropic_messages"}
    ).json()
    assert {item["model_id"] for item in anthropic_only["items"]} == {
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    }

    # 能力筛选。
    vision = client.get(
        "/api/fake-models", headers=admin_headers, params={"capability": "vision"}
    ).json()
    assert all("vision" in item["capabilities"] for item in vision["items"])
    assert len(vision["items"]) > 0

    # 供应商筛选。
    deepseek = client.get(
        "/api/fake-models", headers=admin_headers, params={"provider": "deepseek"}
    ).json()
    assert {item["model_id"] for item in deepseek["items"]} == {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }

    # 创建带完整元数据的模型 + 更新价格。
    created = client.post(
        "/api/fake-models",
        headers=admin_headers,
        json={
            "model_id": "market-test-model",
            "display_name": "广场测试",
            "pricing": {"input": 1.5, "output": 6, "cached_input": 0.15},
            "context_window": 200_000,
            "max_output_tokens": 32_000,
            "capabilities": ["tools", "vision"],
            "billing_tier": "subscription",
            "endpoint_type": "openai_responses",
            "tags": ["测试", "多模态"],
        },
    ).json()
    assert created["input_price_per_million"] == 1.5
    assert created["billing_tier"] == "subscription"
    assert created["endpoint_type"] == "openai_responses"
    assert created["tags"] == ["测试", "多模态"]

    updated = client.patch(
        f"/api/fake-models/{created['id']}",
        headers=admin_headers,
        json={"input_price_per_million": 2.5, "tags": ["更新"]},
    ).json()
    assert updated["input_price_per_million"] == 2.5
    assert updated["tags"] == ["更新"]

    # 标签筛选 + 搜索（model_id/显示名/描述/标签）。
    by_tag = client.get("/api/fake-models", headers=admin_headers, params={"tag": "更新"}).json()
    assert {item["model_id"] for item in by_tag["items"]} == {"market-test-model"}
    by_search = client.get(
        "/api/fake-models", headers=admin_headers, params={"search": "广场测试"}
    ).json()
    assert "market-test-model" in {item["model_id"] for item in by_search["items"]}

    # 停用后默认不出现，include_disabled=true 时出现。
    client.patch(
        f"/api/fake-models/{created['id']}", headers=admin_headers, json={"enabled": False}
    )
    default_list = client.get("/api/fake-models", headers=admin_headers).json()
    assert "market-test-model" not in {item["model_id"] for item in default_list["items"]}
    with_disabled = client.get(
        "/api/fake-models", headers=admin_headers, params={"include_disabled": True}
    ).json()
    assert "market-test-model" in {item["model_id"] for item in with_disabled["items"]}


def test_user_sees_system_models_and_own_private_models(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "model-user")
    listed = client.get("/api/fake-models", headers=headers).json()
    model_ids = {item["model_id"] for item in listed["items"]}
    assert {"deepseek-v4-pro", "deepseek-v4-flash"} <= model_ids

    private = _model(client, headers, "my-private-model", display_name="私有模型")
    assert private["scope"] == "private"
    assert private["owner_user_id"] == client.get("/api/auth/me", headers=headers).json()["id"]

    listed = client.get("/api/fake-models", headers=headers).json()
    assert "my-private-model" in {item["model_id"] for item in listed["items"]}

    other = _create_user(client, admin_headers, "model-user-2")
    other_listed = client.get("/api/fake-models", headers=other).json()
    assert "my-private-model" not in {item["model_id"] for item in other_listed["items"]}
    # 私有模型对其他用户按不存在处理，避免越权探测。
    assert client.get(f"/api/fake-models/{private['id']}", headers=other).status_code == 404


def test_private_model_shadows_system_model_for_owner(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "shadow-user")
    shadow = _model(client, headers, "deepseek-v4-pro", display_name="我的同名模型")

    listed = client.get("/api/fake-models", headers=headers).json()
    matches = [item for item in listed["items"] if item["model_id"] == "deepseek-v4-pro"]
    assert len(matches) == 1
    assert matches[0]["id"] == shadow["id"]
    assert matches[0]["scope"] == "private"

    import app.core.db as database
    from app.services.effective_models import EffectiveModelService

    with database.SessionLocal() as session:
        from app.repositories.models import User

        owner = session.query(User).filter(User.username == "shadow-user").one()
        models = EffectiveModelService().visible_models(session, owner)
        assert [row.model_id for row in models if row.model_id == "deepseek-v4-pro"] == [
            "deepseek-v4-pro"
        ]
        assert all(row.scope.value == "private" or row.owner_user_id is None for row in models)


def test_admin_governs_all_models_but_cannot_transfer_private(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "governed-model-user")
    private = _model(client, headers, "governed-private")

    admin_listed = client.get("/api/fake-models", headers=admin_headers).json()
    assert "governed-private" in {item["model_id"] for item in admin_listed["items"]}

    # 管理员停用私有模型是治理动作，但不能改变归属。
    updated = client.patch(
        f"/api/fake-models/{private['id']}", headers=admin_headers, json={"enabled": False}
    )
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False
    assert updated.json()["owner_user_id"] == private["owner_user_id"]

    # 他人的私有模型对其他用户按不存在处理（404，不暴露资源是否存在）。
    other = _create_user(client, admin_headers, "other-model-user")
    assert (
        client.patch(
            f"/api/fake-models/{private['id']}", headers=other, json={"enabled": True}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/fake-models/{private['id']}", headers=other).status_code == 404


def test_user_can_manage_own_private_model_and_admin_creates_system(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "owner-model-user")
    created = _model(client, headers, "owner-model")
    patched = client.patch(
        f"/api/fake-models/{created['id']}",
        headers=headers,
        json={"display_name": "新显示名", "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "新显示名"
    assert patched.json()["is_enabled"] is False

    assert client.delete(f"/api/fake-models/{created['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/fake-models/{created['id']}", headers=headers).status_code == 404

    system = _model(client, admin_headers, "system-only-model", display_name="系统模型")
    assert system["scope"] == "system"
    assert system["owner_user_id"] is None

    duplicate = client.post(
        "/api/fake-models", headers=admin_headers, json={"model_id": "system-only-model"}
    )
    assert duplicate.status_code == 409


def test_model_group_membership_is_limited_to_visible_models(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "group-user")
    other = _create_user(client, admin_headers, "group-user-2")
    owner_private = _model(client, headers, "group-visible")
    foreign_private = _model(client, other, "group-foreign")

    group = client.post("/api/model-groups", headers=headers, json={"name": "常用模型"}).json()
    assert group["model_ids"] == []

    updated = client.put(
        f"/api/model-groups/{group['id']}/models",
        headers=headers,
        json={"fake_model_ids": [999999]},
    )
    # 不存在的模型 id 属于无效成员。
    assert updated.status_code == 400

    updated = client.put(
        f"/api/model-groups/{group['id']}/models",
        headers=headers,
        json={"fake_model_ids": [int(owner_private["id"]), int(foreign_private["id"])]},
    )
    assert updated.status_code == 400
    assert "可见" in updated.json()["error"]["message"]

    updated = client.put(
        f"/api/model-groups/{group['id']}/models",
        headers=headers,
        json={"fake_model_ids": [int(owner_private["id"])]},
    )
    assert updated.status_code == 200
    assert updated.json()["model_ids"] == ["group-visible"]

    # 系统模型也在可见集合内，可加入分组。
    system_ids = [
        int(item["id"])
        for item in client.get("/api/fake-models", headers=headers).json()["items"]
        if item["scope"] == "system"
    ]
    updated = client.put(
        f"/api/model-groups/{group['id']}/models",
        headers=headers,
        json={"fake_model_ids": [int(owner_private["id"]), *system_ids]},
    )
    assert updated.status_code == 200
    assert set(updated.json()["model_ids"]) == {"group-visible"} | {
        item["model_id"]
        for item in client.get("/api/fake-models", headers=headers).json()["items"]
        if item["scope"] == "system"
    }

    first_page = client.get(
        "/api/fake-models",
        headers=headers,
        params={"group_id": group["id"], "page": 1, "page_size": 1},
    ).json()
    second_page = client.get(
        "/api/fake-models",
        headers=headers,
        params={"group_id": group["id"], "page": 2, "page_size": 1},
    ).json()
    assert first_page["total"] == len(updated.json()["model_ids"])
    assert len(first_page["items"]) == 1
    assert len(second_page["items"]) == 1
    assert first_page["items"][0]["id"] != second_page["items"][0]["id"]

    assert client.delete(f"/api/model-groups/{group['id']}", headers=headers).status_code == 204


def test_group_deletion_blocked_while_enabled_key_references_it(client, admin_headers) -> None:
    import app.core.db as database
    from app.domain.enums import DeliveryMode, ReplyStrategy
    from app.repositories.models import ApiKey, User

    headers = _create_user(client, admin_headers, "group-key-user")
    group = client.post("/api/model-groups", headers=headers, json={"name": "被引用分组"}).json()

    with database.SessionLocal() as session:
        owner = session.query(User).filter(User.username == "group-key-user").one()
        session.add(
            ApiKey(
                owner_user_id=owner.id,
                name="group-key",
                key_hash="hash-group",
                key_prefix="sk-group",
                delivery_mode=DeliveryMode.WEB,
                reply_strategy=ReplyStrategy.HUMAN,
                human_timeout_seconds=300,
                model_group_id=int(group["id"]),
            )
        )
        session.commit()

    assert client.delete(f"/api/model-groups/{group['id']}", headers=headers).status_code == 409

    with database.SessionLocal() as session:
        session.query(ApiKey).filter(ApiKey.key_hash == "hash-group").update({"is_enabled": False})
        session.commit()
    assert client.delete(f"/api/model-groups/{group['id']}", headers=headers).status_code == 204


def test_group_ownership_isolation(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "group-owner-a")
    other = _create_user(client, admin_headers, "group-owner-b")
    group = client.post("/api/model-groups", headers=headers, json={"name": "仅自己可见"}).json()

    # 普通用户能看到公开分组，但看不到他人创建的私有分组。
    other_groups = client.get("/api/model-groups", headers=other).json()["items"]
    assert all(item["id"] != group["id"] for item in other_groups)
    assert client.get(f"/api/model-groups/{group['id']}", headers=other).status_code == 404
    # 管理员治理视图能看到全部分组（含他人私有分组）。
    admin_groups = client.get("/api/model-groups", headers=admin_headers).json()["items"]
    assert any(item["id"] == group["id"] for item in admin_groups)


def test_function_calling_capability_merged_into_tools(client, admin_headers) -> None:
    """历史 function_calling 归并为 tools；白名单外能力被忽略。"""
    created = client.post(
        "/api/fake-models",
        headers=admin_headers,
        json={
            "model_id": "merged-capability",
            "capabilities": ["tools", "function_calling", "vision", "not-a-capability"],
        },
    )
    assert created.status_code in (200, 201), created.text
    assert created.json()["capabilities"] == ["tools", "vision"]

    only_legacy = client.post(
        "/api/fake-models",
        headers=admin_headers,
        json={"model_id": "legacy-capability", "capabilities": ["function_calling"]},
    ).json()
    assert only_legacy["capabilities"] == ["tools"]

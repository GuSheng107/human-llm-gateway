"""M7-A LLM 配置管理测试（docs/API_CONTRACT.md §6）。

覆盖：
- 创建 / 查看 / 修改 / 软删除
- Secret 与 Header 值绝不回显
- 重名校验
- 删除被 API Key 引用返回 409
- 删除被活动任务引用返回 409
- 管理员可见全部配置含 owner
- 连通性测试：network_error / upstream_error / ok
- timeout_seconds 范围校验
- base_url 规范化（去掉尾斜杠）
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from app.services.llm_test_service import ConnTestOutcome

# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _bearer(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def _create_body(
    *,
    name: str = "default",
    protocol: str = "openai_compatible",
    base_url: str = "https://api.example.com/v1",
    api_key: str = "sk-test-123",
    model: str = "gpt-4o-mini",
    timeout_seconds: int = 60,
    headers: list[dict[str, str]] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "enabled": enabled,
    }
    if headers is not None:
        body["headers"] = headers
    return body


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


def test_create_returns_view_without_secret(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "default"
    assert body["protocol"] == "openai_compatible"
    assert body["base_url"] == "https://api.example.com/v1"
    assert body["real_model"] == "gpt-4o-mini"
    assert body["is_enabled"] is True
    assert body["api_key_set"] is True
    assert body["headers"] == []
    assert "api_key" not in body
    assert "secret" not in body


def test_create_strips_trailing_slash(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(base_url="https://api.example.com/v1/"),
    )
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://api.example.com/v1"


def test_create_with_headers_returns_header_names_only(client, created_user) -> None:
    body = _create_body(
        headers=[
            {"name": "X-Org", "value": "alpha"},
            {"name": "X-Trace", "value": "true"},
        ]
    )
    resp = client.post("/api/llm-configs", headers=created_user.headers, json=body)
    assert resp.status_code == 201
    view = resp.json()
    names = sorted(h["name"] for h in view["headers"])
    assert names == ["X-Org", "X-Trace"]
    for header in view["headers"]:
        assert "value" not in header or header.get("value_set") is True
        assert "value" not in header  # 永远不返回明文


def test_create_rejects_authorization_header(client, created_user) -> None:
    body = _create_body(headers=[{"name": "Authorization", "value": "Bearer x"}])
    resp = client.post("/api/llm-configs", headers=created_user.headers, json=body)
    assert resp.status_code == 400
    assert "Authorization" in resp.text


def test_create_rejects_duplicate_name(client, created_user) -> None:
    client.post("/api/llm-configs", headers=created_user.headers, json=_create_body())
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_create_validates_base_url_scheme(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(base_url="ftp://api.example.com/v1"),
    )
    assert resp.status_code == 400
    assert "scheme" in resp.text or "scheme" in resp.json()["error"]["message"]


def test_create_validates_timeout_range(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(timeout_seconds=2),
    )
    # Pydantic ge=5 直接 422；服务层校验在 schema 通过后捕获边界条件
    assert resp.status_code in (400, 422)


def test_create_anthropic_protocol(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(
            name="claude",
            protocol="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-ant-test",
            model="claude-3-5-sonnet",
        ),
    )
    assert resp.status_code == 201
    assert resp.json()["protocol"] == "anthropic"


def test_get_returns_redacted_view(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    resp = client.get(f"/api/llm-configs/{created['id']}", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key_set"] is True
    assert "api_key" not in body
    for key in ("secret", "secret_ciphertext", "headers_ciphertext"):
        assert key not in body


def test_list_owner_isolation(client, created_user) -> None:
    client.post("/api/llm-configs", headers=created_user.headers, json=_create_body(name="a"))
    resp = client.get("/api/llm-configs", headers=created_user.headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert all(item.get("owner_username") is None for item in items)


def test_list_search_by_name(client, created_user) -> None:
    client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(name="alpha"),
    )
    client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(name="beta"),
    )
    resp = client.get(
        "/api/llm-configs?search=alpha",
        headers=created_user.headers,
    )
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["alpha"]


def test_list_admin_sees_owner(client, admin_headers, created_user) -> None:
    client.post("/api/llm-configs", headers=created_user.headers, json=_create_body(name="owned"))
    resp = client.get("/api/llm-configs", headers=admin_headers)
    assert resp.status_code == 200
    matched = [i for i in resp.json()["items"] if i["name"] == "owned"]
    assert matched and matched[0]["owner_username"] == created_user.username


def test_update_changes_fields(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = created["id"]
    resp = client.patch(
        f"/api/llm-configs/{config_id}",
        headers=created_user.headers,
        json={"model": "gpt-4o", "timeout_seconds": 90},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["real_model"] == "gpt-4o"
    assert body["timeout_seconds"] == 90


def test_update_rotates_api_key(client, created_user) -> None:
    """更新 api_key 必须重新加密，不能用旧 secret 命中。"""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret
    from app.repositories.models import LlmConfig

    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    resp = client.patch(
        f"/api/llm-configs/{config_id}",
        headers=created_user.headers,
        json={"api_key": "sk-rotated-999"},
    )
    assert resp.status_code == 200
    with SessionLocal() as session:
        row = session.execute(select(LlmConfig).where(LlmConfig.id == config_id)).scalar_one()
        plaintext = decrypt_secret(row.secret_ciphertext, get_settings().app_secret, "llm-config")
    assert plaintext == "sk-rotated-999"


def test_update_omitting_secret_preserves_existing(client, created_user) -> None:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret
    from app.repositories.models import LlmConfig

    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    client.patch(
        f"/api/llm-configs/{config_id}",
        headers=created_user.headers,
        json={"model": "gpt-4o"},
    )
    with SessionLocal() as session:
        row = session.execute(select(LlmConfig).where(LlmConfig.id == config_id)).scalar_one()
        plaintext = decrypt_secret(row.secret_ciphertext, get_settings().app_secret, "llm-config")
    assert plaintext == "sk-test-123"


def test_update_empty_api_key_string_preserves_secret(client, created_user) -> None:
    """PATCH 显式提交空串/纯空白 api_key 视为保留旧值（与前端"留空保留"一致）。"""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret
    from app.repositories.models import LlmConfig

    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    for empty_value in ("", "   "):
        resp = client.patch(
            f"/api/llm-configs/{config_id}",
            headers=created_user.headers,
            json={"api_key": empty_value},
        )
        assert resp.status_code == 200, resp.text
    with SessionLocal() as session:
        row = session.execute(select(LlmConfig).where(LlmConfig.id == config_id)).scalar_one()
        plaintext = decrypt_secret(row.secret_ciphertext, get_settings().app_secret, "llm-config")
    assert plaintext == "sk-test-123"


def test_update_api_key_strips_whitespace(client, created_user) -> None:
    """api_key 更新时先 strip 再加密。"""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret
    from app.repositories.models import LlmConfig

    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    resp = client.patch(
        f"/api/llm-configs/{config_id}",
        headers=created_user.headers,
        json={"api_key": "  sk-new-42  "},
    )
    assert resp.status_code == 200
    with SessionLocal() as session:
        row = session.execute(select(LlmConfig).where(LlmConfig.id == config_id)).scalar_one()
        plaintext = decrypt_secret(row.secret_ciphertext, get_settings().app_secret, "llm-config")
    assert plaintext == "sk-new-42"


def test_create_rejects_case_conflicting_headers(client, created_user) -> None:
    """Foo 与 foo 在 HTTP 语义中同名：显式 400 而非静默覆盖。"""
    body = _create_body(
        headers=[
            {"name": "X-Org", "value": "a"},
            {"name": "x-org", "value": "b"},
        ]
    )
    resp = client.post("/api/llm-configs", headers=created_user.headers, json=body)
    assert resp.status_code == 400
    assert "冲突" in resp.json()["error"]["message"]


def test_delete_unreferenced_soft_deletes(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = created["id"]
    resp = client.delete(f"/api/llm-configs/{config_id}", headers=created_user.headers)
    assert resp.status_code == 204
    # 列表不再出现
    listing = client.get("/api/llm-configs", headers=created_user.headers).json()
    assert all(item["id"] != config_id for item in listing["items"])
    # 详情 404
    detail = client.get(f"/api/llm-configs/{config_id}", headers=created_user.headers)
    assert detail.status_code == 404


def test_delete_when_api_key_referenced_returns_409(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    key_resp = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={
            "name": "using-llm",
            "delivery_mode": "web",
            "reply_strategy": "llm",
            "llm_config_id": config_id,
        },
    )
    assert key_resp.status_code == 201, key_resp.text
    resp = client.delete(f"/api/llm-configs/{config_id}", headers=created_user.headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_delete_when_active_task_references_returns_409(client, created_user, created_key) -> None:
    """活动任务通过快照引用配置时，删除配置必须返回 409。"""
    created = client.post(
        "/api/llm-configs", headers=created_user.headers, json=_create_body()
    ).json()
    config_id = int(created["id"])
    # 直接构造一个引用该配置的活动任务
    from sqlalchemy import update as sa_update

    import app.core.db as database
    from app.repositories.models import RequestTask

    payload = {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "x"}],
    }
    raw = json.dumps(payload).encode()
    from app.domain.enums import InferenceProtocol
    from app.protocols import chat_completions as chat_protocol
    from app.repositories.models import ApiKey, User
    from app.services.inference_service import InferenceService

    with database.SessionLocal() as session:
        key = session.get(ApiKey, created_key.id)
        owner = session.get(User, created_user.user_id)
        parsed = chat_protocol.parse_request(raw)
        service = InferenceService()
        task = service.create_task(
            session,
            key=key,
            owner=owner,
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.execute(
            sa_update(RequestTask)
            .where(RequestTask.id == task.id)
            .values(llm_config_id_snapshot=config_id)
        )
        session.commit()

    resp = client.delete(f"/api/llm-configs/{config_id}", headers=created_user.headers)
    assert resp.status_code == 409


def test_get_other_user_config_returns_404(client, admin_headers, created_user) -> None:
    """普通用户看不到他人配置：返回 404 而非 403，避免泄露存在性。"""
    import secrets as _secrets

    from sqlalchemy import select

    import app.core.db as database
    from app.repositories.models import User

    other_username = f"o-{_secrets.token_hex(3)}"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": other_username,
            "display_name": other_username,
            "password": "User-Pass1!",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}
    change = client.post(
        "/api/account/password",
        headers=other_headers,
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    login2 = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    with database.SessionLocal() as session:
        other_user = session.execute(
            select(User).where(User.username == other_username)
        ).scalar_one()

    # 当前用户先创建一个配置
    my_cfg = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    ).json()
    # 另一用户查不到（即便提供 id）
    resp = client.get(f"/api/llm-configs/{my_cfg['id']}", headers=other_headers)
    assert resp.status_code == 404
    assert other_user.id != created_user.user_id


# ----------------------------------------------------------------------
# 连通性测试
# ----------------------------------------------------------------------


def _mock_outcome(**kwargs: Any) -> ConnTestOutcome:
    base = {"success": True, "reason_code": "ok", "detail": "ok", "http_status": 200}
    base.update(kwargs)
    return ConnTestOutcome(**base)


def test_connectivity_test_success_records_last_tested_at(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    ).json()
    config_id = created["id"]

    async def fake(**kwargs: Any) -> ConnTestOutcome:
        return _mock_outcome(success=True, reason_code="ok", http_status=200)

    with patch("app.api.llm_configs.run_connectivity_test", side_effect=fake) as runner:
        resp = client.post(f"/api/llm-configs/{config_id}/test", headers=created_user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["reason_code"] == "ok"
    assert body["http_status"] == 200
    assert body["last_tested_at"]
    # 连通性测试必须真实经过 runner（而非未调用即通过）。
    assert runner.await_count == 1


def test_connectivity_test_network_error_returns_failed(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    ).json()
    config_id = created["id"]

    async def fake(**kwargs: Any) -> ConnTestOutcome:
        return _mock_outcome(
            success=False,
            reason_code="network_error",
            detail="网络错误: ConnectError",
            http_status=None,
        )

    with patch("app.api.llm_configs.run_connectivity_test", side_effect=fake):
        resp = client.post(f"/api/llm-configs/{config_id}/test", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["reason_code"] == "network_error"
    assert body["http_status"] is None
    detail = client.get(f"/api/llm-configs/{config_id}", headers=created_user.headers).json()
    assert detail["last_test_result"] == "failed"


def test_connectivity_test_upstream_4xx_returns_failed(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    ).json()
    config_id = created["id"]

    async def fake(**kwargs: Any) -> ConnTestOutcome:
        return _mock_outcome(
            success=False,
            reason_code="upstream_error",
            detail="上游返回 401",
            http_status=401,
        )

    with patch("app.api.llm_configs.run_connectivity_test", side_effect=fake):
        resp = client.post(f"/api/llm-configs/{config_id}/test", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["reason_code"] == "upstream_error"
    assert body["http_status"] == 401


def test_connectivity_test_does_not_echo_secret(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(),
    ).json()
    config_id = created["id"]

    async def fake(**kwargs: Any) -> ConnTestOutcome:
        return _mock_outcome(success=True)

    with patch("app.api.llm_configs.run_connectivity_test", side_effect=fake):
        resp = client.post(f"/api/llm-configs/{config_id}/test", headers=created_user.headers)
    text = resp.text.lower()
    assert "sk-test-123" not in text
    assert "sk-rotated" not in text


def test_connectivity_test_disabled_returns_400(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_create_body(enabled=False),
    ).json()
    config_id = created["id"]
    resp = client.post(f"/api/llm-configs/{config_id}/test", headers=created_user.headers)
    assert resp.status_code == 400


def test_connectivity_test_runner_url_normalization() -> None:
    """URL 拼接：OpenAI /models、Anthropic /v1/messages 由 base_url 直拼。"""
    from app.services.llm_test_service import (
        _normalize_anthropic_url,
        _normalize_openai_models_url,
    )

    assert (
        _normalize_openai_models_url("https://api.example.com/v1/")
        == "https://api.example.com/v1/models"
    )
    assert (
        _normalize_anthropic_url("https://api.anthropic.com/")
        == "https://api.anthropic.com/v1/messages"
    )


def test_connectivity_test_uses_hard_10s_timeout() -> None:
    """无论配置 timeout_seconds 多大，连通性测试使用 10s 上限。"""
    from app.services.llm_test_service import LLM_CONNECT_TEST_TIMEOUT_SECONDS

    assert LLM_CONNECT_TEST_TIMEOUT_SECONDS == 10


def test_connectivity_test_other_user_config_returns_404(
    client, admin_headers, created_user
) -> None:
    """他人配置对当前用户不可见，连通性测试也按 404 处理。"""
    other = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "other-test-user",
            "display_name": "other-test-user",
            "password": "User-Pass1!",
        },
    )
    assert other.status_code == 201, other.text
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": "other-test-user",
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert other_login.status_code == 200
    other_token = other_login.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    change = client.post(
        "/api/account/password",
        headers=other_headers,
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": "other-test-user",
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    other_cfg = client.post(
        "/api/llm-configs",
        headers=other_headers,
        json=_create_body(),
    ).json()

    resp = client.post(f"/api/llm-configs/{other_cfg['id']}/test", headers=created_user.headers)
    assert resp.status_code == 404


def test_create_requires_auth(client) -> None:
    resp = client.post("/api/llm-configs", json=_create_body())
    assert resp.status_code == 401

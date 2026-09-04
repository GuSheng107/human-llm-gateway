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
def stub_dns(monkeypatch):
    """SSRF 校验的 DNS stub：任意主机解析为合成公网 IP（93.184.x.x 段）。

    真实 getaddrinfo 在测试环境不可控（离线/内网 DNS 会 fail-closed 拒绝
    一切公网域名）。SSRF 分档行为由 test_ssrf_and_limits.py 显式覆盖。
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        family = args[0] if args else 0
        proto = args[2] if len(args) > 2 else 6
        # 稳定散列到 93.184.0.0/16 公网段；特殊内网测试名直译。
        if host in ("localhost",):
            ip = "127.0.0.1"
        elif host.startswith("intranet-"):
            ip = "10.0.0.9"
        else:
            digest = sum(ord(c) for c in host)
            ip = f"93.184.{digest % 256}.{(digest * 7) % 254 + 1}"
        return [(family, proto, 6, "", (ip, port or 0))]

    import socket as _socket

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture(autouse=True)
def bypass_captcha(monkeypatch):
    """测试环境绕过图形验证码校验（验证码正确性由独立单元测试覆盖）。"""
    monkeypatch.setattr("app.api.auth.verify_captcha", lambda token, code: True)


@pytest.fixture(autouse=True)
def stub_llm_save_gate(monkeypatch):
    """「启用 LLM 配置前必须连通性测试通过」的默认存根：一律视为成功。

    需要失败语义的用例（tests/test_m7_llm_configs.py）自行 patch 同名函数覆盖。
    """
    from app.services.llm_test_service import ConnTestOutcome

    async def _ok(**kwargs):
        return ConnTestOutcome(True, "ok", "ok", 200)

    monkeypatch.setattr("app.api.llm_configs.run_connectivity_test", _ok)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # 测试环境：日志直写内存 store，避免异步落库线程造成时序抖动。
    from app.core.logging import set_direct_store

    class _MemoryLogStore:
        def __init__(self) -> None:
            self.entries: list[dict] = []

        def append(self, entry: dict) -> None:
            from sqlalchemy import text as _text

            def _int(key: str):
                value = entry.get(key)
                if isinstance(value, bool) or value is None:
                    return None
                try:
                    return int(str(value))
                except (TypeError, ValueError):
                    return None

            import json as _json
            from datetime import UTC, datetime

            with database.SessionLocal() as session:
                session.execute(
                    _text(
                        "INSERT INTO app_logs (level, event, message, request_id, logger,"
                        " user_id, task_id, api_key_id, connection_id, context_json, created_at)"
                        " VALUES (:level, :event, :message, :request_id, :logger, :user_id,"
                        " :task_id, :api_key_id, :connection_id, :context, :created_at)"
                    ),
                    {
                        "level": entry["level"],
                        "event": entry["event"],
                        "message": entry["message"],
                        "request_id": entry.get("request_id"),
                        "logger": entry.get("logger"),
                        "user_id": _int("user_id"),
                        "task_id": _int("task_id"),
                        "api_key_id": _int("api_key_id"),
                        "connection_id": _int("connection_id"),
                        "context": _json.dumps(
                            entry.get("context") or {}, ensure_ascii=False, default=str
                        ),
                        "created_at": datetime.now(tz=UTC),
                    },
                )
                session.commit()
            self.entries.append(entry)

    store = _MemoryLogStore()
    set_direct_store(store)
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    from app.core.logging import get_log_queue

    get_log_queue().direct_store = None
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

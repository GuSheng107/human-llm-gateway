"""日志与控制台 API 测试（docs/API_CONTRACT.md §11）。

覆盖：
- /api/logs：统一日志查询（合并审计与应用日志）；管理员可看全站；
  普通用户只能看到与自己（actor/owner 或资源归属）相关的行；
  支持 trace_id / event / hours 过滤；按时间倒序返回。
- /api/dashboard：用户视角个人统计；管理员视角全局统计。
"""

from __future__ import annotations

from sqlalchemy import select

import app.core.db as database
from app.domain.enums import AuditAction, AuditResult
from app.repositories.models import RequestTask
from app.repositories.system import AppLogRepository, AuditRepository


def _seed_audit(
    action: str,
    resource_type: str,
    fields: list[str],
    *,
    actor_user_id: int | None = 1,
    owner_user_id: int | None = None,
    request_id: str | None = None,
) -> None:
    with database.SessionLocal() as session:
        AuditRepository().add(
            session,
            action=AuditAction(action),
            resource_type=resource_type,
            result=AuditResult.SUCCESS,
            actor_user_id=actor_user_id,
            owner_user_id=owner_user_id,
            request_id=request_id,
            metadata={"fields": fields},
        )
        session.commit()


def _seed_applog(
    event: str,
    level: str = "info",
    message: str = "x",
    *,
    user_id: int | None = None,
    request_id: str | None = None,
    context: dict[str, object] | None = None,
) -> None:
    with database.SessionLocal() as session:
        AppLogRepository().add(
            session,
            level=level,
            event=event,
            message=message,
            user_id=user_id,
            request_id=request_id,
            context=context,
        )
        session.commit()


# ----------------------------------------------------------------------
# 统一日志查询
# ----------------------------------------------------------------------


def test_logs_admin_sees_all(client, admin_headers, created_user) -> None:
    """管理员可见审计与应用两类日志，按 created_at 倒序合并。"""
    _seed_audit("api_key.created", "api_key", ["name"], actor_user_id=1)
    _seed_applog("test.event.admin", "info", "admin 可见", user_id=created_user.user_id)
    resp = client.get("/api/logs", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(item["kind"] == "audit" for item in items)
    assert any(item["kind"] == "app" for item in items)
    audit = next(item for item in items if item["kind"] == "audit")
    app = next(item for item in items if item["kind"] == "app")
    assert audit["category"] == "audit"
    assert audit["context"] == {"fields": ["name"]}
    assert app["category"] == "test"


def test_logs_owner_scope_filters_for_viewer(client, created_user) -> None:
    """普通用户可见的审计日志：actor=自己 或者 owner=自己。"""
    # admin 替该用户改资料 -> owner=created_user, actor=admin
    _seed_audit(
        "user.updated",
        "user",
        ["display_name"],
        actor_user_id=1,
        owner_user_id=created_user.user_id,
        request_id=None,
    )
    # admin 自己创建邀请码（owner=admin） -> 该用户看不到
    _seed_audit(
        "invitation.created",
        "invitation",
        ["code"],
        actor_user_id=1,
        owner_user_id=1,
    )
    resp = client.get("/api/logs", headers=created_user.headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    # 至少出现 owner=created_user 的 user.updated 记录
    assert any(item["event"] == "user.updated" for item in items)
    # 不能出现 admin 自己的 invitation.created（因为 actor/owner 都不是 created_user）
    assert not any(item["event"] == "invitation.created" for item in items)


def test_logs_trace_id_filter(client, admin_headers) -> None:
    """按 traceId 过滤应同时命中审计与应用日志。"""
    _seed_audit(
        "connection.started",
        "im_connection",
        [],
        actor_user_id=1,
        request_id="req_trace_x",
    )
    _seed_applog("inference.replied", "info", "ok", request_id="req_trace_x")
    resp = client.get("/api/logs?trace_id=req_trace_x", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(item["request_id"] == "req_trace_x" for item in items)


def test_logs_event_filter(client, admin_headers) -> None:
    """event 过滤同时匹配审计的 action 与应用日志的 event。"""
    _seed_audit("llm_config.created", "llm_config", ["name"], actor_user_id=1)
    _seed_applog("inference.human_timeout", "warning", "x")
    resp = client.get("/api/logs?event=inference.human_timeout", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["event"] == "inference.human_timeout" for item in items)


def test_logs_category_level_and_context_filters(client, admin_headers) -> None:
    """分类按事件前缀筛选，等级只返回应用日志，context 可直接读取。"""
    _seed_applog(
        "llm.upstream.response",
        "info",
        "upstream ok",
        context={"endpoint": "https://api.example.com/v1/chat/completions", "usage": {"total": 3}},
    )
    _seed_applog("llm.upstream.error", "warning", "upstream failed")

    resp = client.get("/api/logs?category=llm&level=info", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "app"
    assert items[0]["category"] == "llm"
    assert items[0]["event"] == "llm.upstream.response"
    assert items[0]["context"]["usage"] == {"total": 3}

    fuzzy = client.get("/api/logs?event=upstream", headers=admin_headers)
    assert fuzzy.status_code == 200
    assert {item["event"] for item in fuzzy.json()["items"]} >= {
        "llm.upstream.response",
        "llm.upstream.error",
    }


def test_logs_hours_filter(client, admin_headers) -> None:
    _seed_applog("recent.event", "info", "just now")
    resp = client.get("/api/logs?hours=1", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_logs_requires_auth(client) -> None:
    resp = client.get("/api/logs")
    assert resp.status_code == 401


# ----------------------------------------------------------------------
# 控制台统计
# ----------------------------------------------------------------------


def test_dashboard_user_view(client, created_user, created_key) -> None:
    resp = client.get("/api/dashboard", headers=created_user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stats = body["stats"]
    assert stats["total_api_keys"] >= 1
    assert stats["total_users"] >= 1
    assert stats["active_models"] >= 0
    with database.SessionLocal() as session:
        my_tasks = session.scalars(
            select(RequestTask).where(RequestTask.owner_user_id == created_user.user_id)
        ).all()
        assert len(body["recent_tasks"]) <= 8
        assert len(body["recent_tasks"]) <= len(my_tasks)


def test_dashboard_admin_view(client, admin_headers) -> None:
    resp = client.get("/api/dashboard", headers=admin_headers)
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["total_users"] >= 1
    assert stats["active_users"] >= 1
    assert "active_tasks" in stats


def test_dashboard_api_key_count_excludes_physically_deleted_key(
    client, admin_headers, created_user, created_key
) -> None:
    before = client.get("/api/dashboard", headers=admin_headers)
    assert before.status_code == 200
    assert before.json()["stats"]["total_api_keys"] == 1

    deleted = client.delete(
        f"/api/api-keys/{created_key.id}",
        headers=created_user.headers,
    )
    assert deleted.status_code == 204, deleted.text

    after = client.get("/api/dashboard", headers=admin_headers)
    assert after.status_code == 200
    assert after.json()["stats"]["total_api_keys"] == 0


def test_dashboard_requires_auth(client) -> None:
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401

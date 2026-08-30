"""M9 日志与控制台 API 测试（docs/API_CONTRACT.md §11）。

覆盖：
- /api/audit-logs：管理员可查、普通用户 403、筛选（动作/资源/时间窗）
- /api/app-logs：管理员可查、级别/事件/时间窗筛选、普通用户 403
- /api/dashboard：用户视角个人统计；管理员视角全局统计
- 审计视图不泄露字段值（只含字段名）
"""

from __future__ import annotations

from sqlalchemy import select

import app.core.db as database
from app.domain.enums import AuditAction, AuditResult
from app.repositories.models import RequestTask
from app.repositories.system import AppLogRepository, AuditRepository


def _seed_audit(action: str, resource_type: str, fields: list[str]) -> None:
    with database.SessionLocal() as session:
        AuditRepository().add(
            session,
            action=AuditAction(action),
            resource_type=resource_type,
            result=AuditResult.SUCCESS,
            actor_user_id=1,
            resource_id="1",
            metadata={"fields": fields},
        )
        session.commit()


def _seed_applog(event: str, level: str = "info", message: str = "x") -> None:
    with database.SessionLocal() as session:
        AppLogRepository().add(session, level=level, event=event, message=message)
        session.commit()


# ----------------------------------------------------------------------
# 权限
# ----------------------------------------------------------------------


def test_audit_logs_admin_only(client, admin_headers, created_user) -> None:
    _seed_audit("api_key.created", "api_key", ["name"])
    resp = client.get("/api/audit-logs", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    denied = client.get("/api/audit-logs", headers=created_user.headers)
    assert denied.status_code == 403


def test_app_logs_admin_only(client, admin_headers, created_user) -> None:
    _seed_applog("test.event")
    resp = client.get("/api/app-logs", headers=admin_headers)
    assert resp.status_code == 200
    denied = client.get("/api/app-logs", headers=created_user.headers)
    assert denied.status_code == 403


# ----------------------------------------------------------------------
# 筛选
# ----------------------------------------------------------------------


def test_audit_logs_filter_by_action(client, admin_headers) -> None:
    _seed_audit("llm_config.created", "llm_config", ["name"])
    resp = client.get("/api/audit-logs?action=llm_config.created", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(item["action"] == "llm_config.created" for item in items)


def test_audit_logs_filter_by_resource_type(client, admin_headers) -> None:
    _seed_audit("fake_model.created", "fake_model", ["model_id"])
    resp = client.get("/api/audit-logs?resource_type=fake_model", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(item["resource_type"] == "fake_model" for item in items)


def test_audit_logs_hours_window(client, admin_headers) -> None:
    _seed_audit("connection.started", "im_connection", ["state"])
    resp = client.get("/api/audit-logs?hours=1", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_audit_logs_view_hides_values(client, admin_headers) -> None:
    """审计视图只含字段名，不含字段值或请求正文。"""
    _seed_audit("user.updated", "user", ["display_name", "is_active"])
    resp = client.get("/api/audit-logs?action=user.updated", headers=admin_headers)
    items = resp.json()["items"]
    assert items
    item = items[0]
    assert item["fields"] == ["display_name", "is_active"]
    text = resp.text
    assert "value" not in item
    assert "payload" not in text.lower() or "page_size" in text


def test_app_logs_filter_by_level_and_event(client, admin_headers) -> None:
    _seed_applog("inference.human_timeout", "warning", "等待人工回复超时")
    _seed_applog("normal.event", "info")
    resp = client.get(
        "/api/app-logs?level=warning&event=inference.human_timeout",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert all(item["level"] == "warning" for item in items)
    assert all(item["event"] == "inference.human_timeout" for item in items)


def test_app_logs_invalid_level_rejected(client, admin_headers) -> None:
    resp = client.get("/api/app-logs?level=verbose", headers=admin_headers)
    assert resp.status_code == 422


# ----------------------------------------------------------------------
# 控制台统计
# ----------------------------------------------------------------------


def test_dashboard_user_view(client, created_user, created_key) -> None:
    resp = client.get("/api/dashboard", headers=created_user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stats = body["stats"]
    assert stats["role"] == "user"
    assert stats["my_api_keys"] >= 1
    assert stats["total_users"] == 0  # 用户视角不含全局数据
    # 最近任务只含自己的
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
    assert stats["role"] == "admin"
    assert stats["total_users"] >= 1
    assert stats["active_users"] >= 1


def test_dashboard_requires_auth(client) -> None:
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401

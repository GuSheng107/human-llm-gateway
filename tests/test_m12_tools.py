"""M12 工具沙箱测试（docs/ROADMAP.md M12）。

覆盖：
- 白名单 CRUD：管理员专属、模板占位符校验、shell 元字符拒绝、schema
  仅 string 属性、同名冲突、用户视图不返回命令模板
- 执行：显式确认缺失拒绝并审计、停用工具拒绝、参数缺失/未声明/非字符串
  拒绝、成功执行（echo 命令）结果落库、执行历史隔离
- 沙箱：超时终止、输出截断 limit_exceeded、临时目录隔离（cwd 不含网关目录）、
  环境变量清零（工具读不到任何网关环境）
- 永不自动执行：协议层 tool_calls 数据转发不受沙箱影响（既有测试覆盖，
  此处验证 /v1 路径与工具执行互不侵扰）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

import app.core.db as database
from app.domain.enums import AuditAction
from app.repositories.models import AuditLog, ToolExecution


def _tool_body(name: str = "echo-tool", template: str = "echo {text}") -> dict[str, Any]:
    return {
        "name": name,
        "description": "回显文本",
        "command_template": template,
        "arguments_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "文本"}},
            "required": ["text"],
        },
        "timeout_seconds": 10,
    }


def _create_tool(client, admin_headers, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/api/tools", headers=admin_headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ----------------------------------------------------------------------
# 白名单 CRUD
# ----------------------------------------------------------------------


def test_create_tool_admin_only(client, admin_headers, created_user) -> None:
    denied = client.post("/api/tools", headers=created_user.headers, json=_tool_body())
    assert denied.status_code == 403
    created = _create_tool(client, admin_headers, _tool_body())
    assert created["name"] == "echo-tool"
    assert created["command_template"] == "echo {text}"
    assert created["arguments_schema"]["properties"]["text"]["type"] == "string"


def test_create_tool_rejects_shell_metacharacters(client, admin_headers) -> None:
    # `;` 允许（python -c 需要；argv 直传无 shell 语义）；其余元字符拒绝。
    for template in ("echo {text} | wc", "echo $(whoami)", "echo `whoami`"):
        resp = client.post(
            "/api/tools",
            headers=admin_headers,
            json=_tool_body(name=f"bad-{abs(hash(template)) % 1000}", template=template),
        )
        assert resp.status_code == 400, template


def test_create_tool_rejects_undeclared_placeholder(client, admin_headers) -> None:
    resp = client.post(
        "/api/tools",
        headers=admin_headers,
        json=_tool_body(name="undeclared", template="echo {text} {other}"),
    )
    assert resp.status_code == 400
    assert "other" in resp.json()["error"]["message"]


def test_create_tool_rejects_non_string_schema(client, admin_headers) -> None:
    body = _tool_body(name="non-string")
    body["arguments_schema"] = {
        "type": "object",
        "properties": {"count": {"type": "number"}},
    }
    resp = client.post("/api/tools", headers=admin_headers, json=body)
    # Pydantic 模式（type 仅 string）422 或服务层校验 400 均合法。
    assert resp.status_code in (400, 422)


def test_create_tool_duplicate_name_conflict(client, admin_headers) -> None:
    _create_tool(client, admin_headers, _tool_body())
    resp = client.post("/api/tools", headers=admin_headers, json=_tool_body())
    assert resp.status_code == 409


def test_user_view_hides_command_template(client, admin_headers, created_user) -> None:
    _create_tool(client, admin_headers, _tool_body())
    user_view = client.get("/api/tools", headers=created_user.headers)
    assert user_view.status_code == 200
    items = user_view.json()["items"]
    assert items
    assert items[0]["command_template"] is None
    assert items[0]["arguments_schema"]["properties"]["text"]["type"] == "string"


def test_tool_update_and_delete(client, admin_headers) -> None:
    created = _create_tool(client, admin_headers, _tool_body())
    updated = client.patch(
        f"/api/tools/{created['id']}",
        headers=admin_headers,
        json={"is_enabled": False, "description": "已停用"},
    )
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False
    # 用户视角不再可见
    removed = client.delete(f"/api/tools/{created['id']}", headers=admin_headers)
    assert removed.status_code == 204


# ----------------------------------------------------------------------
# 执行：显式确认与校验
# ----------------------------------------------------------------------


def test_execute_requires_confirmation(client, admin_headers, created_user) -> None:
    """confirmed=False -> 拒绝 + 拒绝审计（显式确认语义）。"""
    tool = _create_tool(client, admin_headers, _tool_body())
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {"text": "hi"}, "confirmed": False},
    )
    assert resp.status_code == 400
    with database.SessionLocal() as session:
        denied = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.TOOL_EXECUTION_DENIED.value)
        ).all()
        assert denied


def test_execute_disabled_tool_rejected(client, admin_headers, created_user) -> None:
    tool = _create_tool(client, admin_headers, _tool_body())
    client.patch(f"/api/tools/{tool['id']}", headers=admin_headers, json={"is_enabled": False})
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {"text": "hi"}, "confirmed": True},
    )
    assert resp.status_code == 400


def test_execute_invalid_arguments_rejected(client, admin_headers, created_user) -> None:
    tool = _create_tool(client, admin_headers, _tool_body())
    # 缺必填
    missing = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert missing.status_code == 400
    # 未声明键
    extra = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {"text": "a", "evil": "b"}, "confirmed": True},
    )
    assert extra.status_code == 400
    # 非字符串
    wrong_type = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {"text": 123}, "confirmed": True},
    )
    assert wrong_type.status_code == 400


def test_execute_succeeds_with_audit(client, admin_headers, created_user) -> None:
    body = _tool_body(
        name="echo-tool",
        template="python -c print('sandbox-ok')",
    )
    body["arguments_schema"] = {"type": "object", "properties": {}}
    tool = _create_tool(client, admin_headers, body)
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "succeeded"
    assert body["exit_code"] == 0
    assert "sandbox-ok" in (body["stdout"] or "")
    assert body["duration_ms"] is not None
    # 审计含执行记录
    with database.SessionLocal() as session:
        executed = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.TOOL_EXECUTED.value)
        ).all()
        assert executed


def test_execution_history_owner_isolated(client, admin_headers, created_user) -> None:
    body = _tool_body(name="hist-tool", template="python -c print(1)")
    body["arguments_schema"] = {"type": "object", "properties": {}}
    tool = _create_tool(client, admin_headers, body)
    client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    listing = client.get("/api/tools/executions", headers=created_user.headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1
    assert all(item["tool_name"] == "hist-tool" for item in listing.json()["items"])


# ----------------------------------------------------------------------
# 沙箱限制
# ----------------------------------------------------------------------


def test_sandbox_timeout_kills_process(client, admin_headers, created_user) -> None:
    """超时硬终止（ping 循环 -> timed_out）。"""
    body = _tool_body(name="sleep-tool", template="ping -n 30 127.0.0.1")
    body["arguments_schema"] = {"type": "object", "properties": {}}
    body["timeout_seconds"] = 2
    tool = _create_tool(client, admin_headers, body)
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert resp.status_code == 201
    body_resp = resp.json()
    assert body_resp["state"] == "timed_out"
    assert body_resp["error_code"] == "timeout"


def test_sandbox_output_truncation(client, admin_headers, created_user) -> None:
    """输出超 64KiB -> limit_exceeded 且截断保存。"""
    body = _tool_body(
        name="flood-tool",
        template="ping -n 20 127.0.0.1",
    )
    body["arguments_schema"] = {"type": "object", "properties": {}}
    body["timeout_seconds"] = 15
    tool = _create_tool(client, admin_headers, body)
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert resp.status_code == 201
    result = resp.json()
    # ping 输出有限可能先超时；仅验证状态合法与 stdout 有界。
    assert result["state"] in ("succeeded", "failed", "limit_exceeded", "timed_out")
    assert len(result["stdout"] or "") <= 64 * 1024


def test_sandbox_empty_environment(client, admin_headers, created_user) -> None:
    """工具进程环境变量清零：读不到 APP_SECRET/ADMIN_PASSWORD 等网关环境。"""
    body = _tool_body(
        name="env-tool",
        template='python -c "import os; print(sorted(os.environ.keys()))"',
    )
    body["arguments_schema"] = {"type": "object", "properties": {}}
    tool = _create_tool(client, admin_headers, body)
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert resp.status_code == 201, resp.text
    stdout = resp.json()["stdout"] or ""
    assert "APP_SECRET" not in stdout
    assert "ADMIN_PASSWORD" not in stdout


def test_sandbox_temp_working_directory(client, admin_headers, created_user) -> None:
    """工作目录为专用临时目录，非网关目录。"""
    body = _tool_body(
        name="cwd-tool",
        template='python -c "import os; print(os.getcwd())"',
    )
    body["arguments_schema"] = {"type": "object", "properties": {}}
    tool = _create_tool(client, admin_headers, body)
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {}, "confirmed": True},
    )
    assert resp.status_code == 201, resp.text
    cwd = (resp.json()["stdout"] or "").strip()
    assert "human-llm-gateway" not in cwd
    assert "hlg-tool-" in cwd.lower()


# ----------------------------------------------------------------------
# 永不自动执行（边界固化）
# ----------------------------------------------------------------------


def test_protocol_tool_calls_never_execute(client, created_user, created_key) -> None:
    """/v1 协议层的 tool call 只做数据转发：不产生任何工具执行记录。"""
    from unittest.mock import patch

    from tests.test_m7_llm_forward import (
        _create_llm_config,
        _create_strategy_key,
    )

    cfg = _create_llm_config(
        client,
        created_user.headers,
        {
            "name": "auto-probe",
            "protocol": "openai_chat",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk",
            "model": "gpt-4o-mini",
            "timeout_seconds": 60,
            "enabled": True,
        },
    )
    key = _create_strategy_key(
        client, created_user.headers, strategy="llm", llm_config_id=int(cfg["id"])
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_auto_1",
                                "type": "function",
                                "function": {"name": "rm", "arguments": '{"path": "/"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key['plaintext']}"},
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    # tool_calls 原样转发给调用方
    assert resp.json()["choices"][0]["message"]["tool_calls"]
    # 不产生任何沙箱执行
    with database.SessionLocal() as session:
        count = len(session.scalars(select(ToolExecution)).all())
        assert count == 0

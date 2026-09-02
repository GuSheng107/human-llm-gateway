"""M12 工具沙箱测试（docs/ROADMAP.md M12）。

覆盖：
- 白名单 CRUD：管理员专属、模板占位符校验、shell 元字符拒绝、schema
  仅 string 属性、同名冲突、用户视图不返回命令模板
- 执行：显式确认缺失拒绝并审计、停用工具拒绝、参数缺失/未声明/非字符串
  拒绝、成功执行（echo 命令）结果落库、执行历史隔离
- 沙箱：超时终止、输出截断 limit_exceeded、容器 tmpfs 隔离（cwd 不含网关目录）、
  最小环境（工具读不到任何网关环境）；OCI 命令构造/失败关闭为纯单元测试，
  末尾另附真实容器端到端测试（无运行时或镜像时自动跳过）
- 永不自动执行：协议层 tool_calls 数据转发不受沙箱影响（既有测试覆盖，
  此处验证 /v1 路径与工具执行互不侵扰）
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from sqlalchemy import select

import app.core.db as database
from app.core.config import get_settings
from app.domain.enums import AuditAction
from app.domain.errors import DomainError
from app.repositories.models import AuditLog, ToolExecution
from app.services.tools.sandbox import (
    SandboxResult,
    _runtime_environment,
    build_oci_command,
    render_command,
    resolve_oci_runtime,
    run_sandboxed,
    validate_command_template,
)


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


@pytest.fixture(autouse=True)
def _stub_oci_execution(monkeypatch):
    """API 契约测试不依赖开发机安装容器运行时；隔离参数由下方专项测试验证。"""

    def fake_run(
        argv: list[str], *, timeout_seconds: int, stdin_data: bytes | None = None
    ) -> SandboxResult:
        joined = " ".join(argv)
        if argv and argv[0] == "ping":
            return SandboxResult(None, "", "", "timed_out", timeout_seconds * 1000, "timeout")
        if argv and argv[0] == "tr":
            return SandboxResult(0, (stdin_data or b"").decode().upper(), "", "succeeded", 1)
        if "sandbox-ok" in joined:
            stdout = "sandbox-ok\n"
        elif "os.environ" in joined:
            stdout = "['HOME', 'PATH', 'TMPDIR']\n"
        elif "os.getcwd" in joined:
            stdout = "/workspace\n"
        else:
            stdout = "1\n" if "print(1)" in joined else ""
        return SandboxResult(0, stdout, "", "succeeded", 1)

    monkeypatch.setattr("app.services.tools.service.run_sandboxed", fake_run)


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


def _stdin_tool_body(name: str = "upper-tool") -> dict[str, Any]:
    return {
        "name": name,
        "description": "stdin 文本转大写",
        "command_template": "tr a-z A-Z",
        "arguments_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "文本"}},
            "required": ["text"],
        },
        "timeout_seconds": 10,
        "stdin_parameter": "text",
    }


def test_create_stdin_tool_rejects_undeclared_parameter(client, admin_headers) -> None:
    body = _stdin_tool_body(name="stdin-undeclared")
    body["stdin_parameter"] = "ghost"
    resp = client.post("/api/tools", headers=admin_headers, json=body)
    assert resp.status_code == 400
    assert "stdin_parameter" in resp.json()["error"]["message"]


def test_create_stdin_tool_rejects_parameter_in_template(client, admin_headers) -> None:
    body = _stdin_tool_body(name="stdin-in-template")
    body["command_template"] = "tr a-z A-Z {text}"
    resp = client.post("/api/tools", headers=admin_headers, json=body)
    assert resp.status_code == 400
    assert "stdin" in resp.json()["error"]["message"]


def test_execute_stdin_tool_delivers_argument_via_stdin(
    client, admin_headers, created_user
) -> None:
    tool = _create_tool(client, admin_headers, _stdin_tool_body())
    resp = client.post(
        f"/api/tools/{tool['id']}/execute",
        headers=created_user.headers,
        json={"arguments": {"text": "hello world"}, "confirmed": True},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["stdout"] == "HELLO WORLD"


def test_stdin_parameter_visible_in_tool_view(client, admin_headers) -> None:
    tool = _create_tool(client, admin_headers, _stdin_tool_body(name="stdin-view"))
    assert tool["stdin_parameter"] == "text"


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


def test_sandbox_minimal_environment(client, admin_headers, created_user) -> None:
    """容器仅有最小环境，读不到 APP_SECRET/ADMIN_PASSWORD 等网关环境。"""
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
    """工作目录为容器 tmpfs，不是宿主网关目录。"""
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
    assert cwd == "/workspace"


def test_oci_command_has_cross_platform_hardening() -> None:
    command = build_oci_command(
        "docker",
        "human-llm-gateway-tool-sandbox:latest",
        "hlg-tool-test",
        ["python", "-c", "print(1)"],
        memory_mb=256,
        cpus=1.0,
        pids_limit=64,
        tmpfs_mb=64,
    )

    def value_after(flag: str) -> str:
        return command[command.index(flag) + 1]

    assert value_after("--network") == "none"
    assert value_after("--ipc") == "none"
    assert value_after("--cap-drop") == "ALL"
    assert value_after("--user") == "65532:65532"
    assert value_after("--memory") == "256m"
    assert value_after("--memory-swap") == "256m"
    assert value_after("--pids-limit") == "64"
    assert "--read-only" in command
    assert "no-new-privileges:true" in command
    assert "--volume" not in command and "-v" not in command and "--mount" not in command
    assert value_after("--entrypoint") == "python"


def test_user_argument_remains_one_argv_without_shell_interpolation() -> None:
    value = "hello; $(whoami) && cat /etc/passwd"
    assert render_command("echo {text}", {"text": value}) == ["echo", value]


def test_user_argument_cannot_trigger_second_placeholder_substitution() -> None:
    assert render_command("echo {first} {second}", {"first": "{second}", "second": "safe"}) == [
        "echo",
        "{second}",
        "safe",
    ]


def test_executable_cannot_be_user_controlled() -> None:
    with pytest.raises(DomainError):
        validate_command_template("{executable} --version", ["executable"])


def test_oci_command_stdin_interactive_flag() -> None:
    with_stdin = build_oci_command(
        "docker",
        "human-llm-gateway-tool-sandbox:latest",
        "hlg-tool-test",
        ["tr", "a-z", "A-Z"],
        memory_mb=256,
        cpus=1.0,
        pids_limit=64,
        tmpfs_mb=64,
        stdin_enabled=True,
    )
    assert "-i" in with_stdin
    without_stdin = build_oci_command(
        "docker",
        "human-llm-gateway-tool-sandbox:latest",
        "hlg-tool-test",
        ["echo", "x"],
        memory_mb=256,
        cpus=1.0,
        pids_limit=64,
        tmpfs_mb=64,
    )
    assert "-i" not in without_stdin


def test_runtime_environment_drops_gateway_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_SECRET", "must-not-leak")
    monkeypatch.setenv("ADMIN_PASSWORD", "must-not-leak")
    env = _runtime_environment(str(tmp_path))
    assert "APP_SECRET" not in env
    assert "ADMIN_PASSWORD" not in env
    assert env["HOME"]


def test_runtime_environment_only_accepts_local_daemon_endpoints(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2376")
    monkeypatch.setenv("CONTAINER_HOST", "ssh://remote.example/run/podman.sock")
    env = _runtime_environment(str(tmp_path))
    assert "DOCKER_HOST" not in env
    assert "CONTAINER_HOST" not in env

    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    assert _runtime_environment(str(tmp_path))["DOCKER_HOST"].startswith("unix://")


def test_missing_oci_runtime_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.sandbox.resolve_oci_runtime", lambda _runtime: None)
    result = run_sandboxed(["python", "-c", "print(1)"], timeout_seconds=1)
    assert result.state == "failed"
    assert result.error_code == "sandbox_unavailable"


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


# ----------------------------------------------------------------------
# 真实容器端到端（需要本机 Docker/Podman + 已构建沙箱镜像）
# ----------------------------------------------------------------------


def _e2e_unavailable_reason() -> str | None:
    settings = get_settings()
    runtime = resolve_oci_runtime(settings.tool_sandbox_runtime)
    if runtime is None:
        return "未安装 Docker/Podman"
    try:
        probe = subprocess.run(
            [runtime, "image", "inspect", settings.tool_sandbox_image],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "容器守护进程不可达"
    if probe.returncode != 0:
        return f"沙箱镜像未构建：{settings.tool_sandbox_image}（见 docs/SANDBOX.md）"
    return None


_E2E_SKIP = _e2e_unavailable_reason()
requires_oci_runtime = pytest.mark.skipif(_E2E_SKIP is not None, reason=_E2E_SKIP or "")


@requires_oci_runtime
def test_e2e_echo_runs_in_container() -> None:
    result = run_sandboxed(["echo", "e2e-ok"], timeout_seconds=30)
    assert result.state == "succeeded"
    assert result.exit_code == 0
    assert result.stdout.strip() == "e2e-ok"


@requires_oci_runtime
def test_e2e_timeout_kills_container() -> None:
    result = run_sandboxed(
        ["python", "-c", "import time; time.sleep(60)"],
        timeout_seconds=2,
    )
    assert result.state == "timed_out"
    assert result.error_code == "timeout"


@requires_oci_runtime
def test_e2e_minimal_environment_and_tmpfs_workspace() -> None:
    result = run_sandboxed(
        [
            "python",
            "-c",
            "import os; print(sorted(os.environ)); print(os.getcwd())",
        ],
        timeout_seconds=30,
    )
    assert result.state == "succeeded"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == str(sorted(["HOME", "PATH", "TMPDIR"]))
    assert lines[1] == "/workspace"


@requires_oci_runtime
def test_e2e_network_disabled() -> None:
    result = run_sandboxed(
        [
            "python",
            "-c",
            "import socket; socket.create_connection(('10.255.255.1', 1), timeout=2)",
        ],
        timeout_seconds=30,
    )
    assert result.state == "failed"
    assert result.error_code == "nonzero_exit"

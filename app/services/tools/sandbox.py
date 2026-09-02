"""OCI 工具沙箱（M12）。

所有白名单命令都在本机 Docker 或 Podman 的 Linux 容器中执行。Windows、
macOS 与 Linux 共用同一隔离契约；找不到容器运行时或镜像时失败关闭，绝不
回退到宿主进程。容器无网络、无宿主挂载、只读根文件系统、无 capabilities，
并受 CPU、内存、PID、文件描述符、时间和输出大小限制。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, BinaryIO

from ...core.config import get_settings
from ...core.constants import TOOL_MAX_ARGUMENT_VALUE_LENGTH, TOOL_MAX_STDOUT_BYTES
from ...core.logging import log_event
from ...domain.errors import DomainError, DomainErrorCode


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    state: str
    duration_ms: int
    error_code: str | None = None


def _validation_error(message: str) -> DomainError:
    return DomainError(DomainErrorCode.VALIDATION_FAILED, message, status_code=400)


def _parse_template(command_template: str) -> list[str]:
    try:
        parts = shlex.split(command_template, posix=False)
    except ValueError as exc:
        raise _validation_error("命令模板引号不匹配") from exc
    parsed: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}:
            parsed.append(part[1:-1])
        else:
            parsed.append(part)
    if not parsed:
        raise _validation_error("命令为空")
    return parsed


def render_command(command_template: str, arguments: dict[str, Any]) -> list[str]:
    """把已校验参数替换进 argv；整个过程不经过 shell。"""
    checked: dict[str, str] = {}
    for key, value in arguments.items():
        if not isinstance(value, str):
            raise _validation_error(f"参数 {key} 必须是字符串")
        if len(value) > TOOL_MAX_ARGUMENT_VALUE_LENGTH:
            raise _validation_error(f"参数 {key} 最多 {TOOL_MAX_ARGUMENT_VALUE_LENGTH} 字符")
        if "\x00" in value:
            raise _validation_error(f"参数 {key} 不能包含 NUL")
        checked[key] = value

    placeholder_pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in checked:
            raise _validation_error(f"命令模板参数 {key} 未赋值")
        return checked[key]

    rendered: list[str] = []
    for token in _parse_template(command_template):
        rendered.append(placeholder_pattern.sub(replace, token))
    return rendered


def validate_command_template(command_template: str, argument_names: list[str]) -> None:
    """保存白名单时校验模板语法、占位符和静态可执行文件。"""
    placeholders = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", command_template))
    unknown = placeholders - set(argument_names)
    if unknown:
        raise _validation_error(f"命令模板包含未声明的参数占位符: {', '.join(sorted(unknown))}")
    if any(control in command_template for control in ("\x00", "\r", "\n")):
        raise _validation_error("命令模板不能包含控制字符或换行")
    for forbidden in ("|", "`", "$(", "&&", "||", ">>", ">", "<"):
        if forbidden in command_template:
            raise _validation_error(f"命令模板不允许 shell 元字符: {forbidden}")
    argv = _parse_template(command_template)
    if "{" in argv[0] or "}" in argv[0] or argv[0].startswith("-"):
        raise _validation_error("命令模板的可执行文件必须是管理员配置的静态值")


def resolve_oci_runtime(requested: str) -> str | None:
    candidates = ("docker", "podman") if requested == "auto" else (requested,)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def build_oci_command(
    runtime: str,
    image: str,
    container_name: str,
    argv: list[str],
    *,
    memory_mb: int,
    cpus: float,
    pids_limit: int,
    tmpfs_mb: int,
    stdin_enabled: bool = False,
) -> list[str]:
    """构造 Docker/Podman 共同支持的失败关闭执行命令。

    stdin_enabled 时追加 ``-i``，使容器的 stdin 与宿主进程管道连通；
    关闭方式（EOF）即结束输入。
    """
    if not argv:
        raise _validation_error("命令为空")
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise _validation_error("OCI 镜像引用非法")
    result = [
        runtime,
        "run",
        "--rm",
        *(["-i"] if stdin_enabled else []),
        "--name",
        container_name,
        "--pull=never",
        "--network",
        "none",
        "--ipc",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(pids_limit),
        "--memory",
        f"{memory_mb}m",
        "--memory-swap",
        f"{memory_mb}m",
        "--cpus",
        str(cpus),
        "--ulimit",
        "nofile=64:64",
        "--user",
        "65532:65532",
        "--hostname",
        "sandbox",
        "--workdir",
        "/workspace",
        "--tmpfs",
        f"/workspace:rw,nosuid,nodev,size={tmpfs_mb}m",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,noexec,size={tmpfs_mb}m",
        "--tmpfs",
        "/run:rw,nosuid,nodev,noexec,size=8m",
        "--env",
        "HOME=/workspace",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "--log-driver",
        "none",
        "--stop-timeout",
        "1",
        "--init",
        "--entrypoint",
        argv[0],
        image,
        *argv[1:],
    ]
    return result


def _runtime_environment(config_dir: str) -> dict[str, str]:
    """只给本机容器 CLI 必要宿主变量，不转交网关 Secret。"""
    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "DOCKER_CONFIG",
    )
    env = {name: os.environ[name] for name in allowed if os.environ.get(name)}
    env["HOME"] = env.get("HOME") or env.get("USERPROFILE") or config_dir
    windows_pipe_prefix = "\\\\.\\pipe" + "\\"
    for endpoint_name in ("DOCKER_HOST", "CONTAINER_HOST"):
        endpoint = os.environ.get(endpoint_name, "")
        if endpoint.startswith(("unix://", "npipe://", "/", windows_pipe_prefix)):
            env[endpoint_name] = endpoint
    return env


def _capture_stream(stream: BinaryIO, target: bytearray, exceeded: threading.Event) -> None:
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        remaining = TOOL_MAX_STDOUT_BYTES + 1 - len(target)
        if remaining > 0:
            target.extend(chunk[:remaining])
        if len(target) > TOOL_MAX_STDOUT_BYTES:
            exceeded.set()


def _kill_process_tree(process: subprocess.Popen[bytes], env: dict[str, str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _cleanup_container(runtime: str, name: str, env: dict[str, str]) -> None:
    try:
        subprocess.run(
            [runtime, "rm", "--force", name],
            capture_output=True,
            check=False,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def run_sandboxed(
    argv: list[str], *, timeout_seconds: int, stdin_data: bytes | None = None
) -> SandboxResult:
    """在受限 OCI 容器中执行 argv；任何运行时问题都不会回退宿主执行。

    stdin_data 非 None 时以 ``docker run -i`` 建立 stdin 管道并把数据
    写入后关闭（EOF）；输入经 stdin 传递，不经 shell 解释。
    """
    settings = get_settings()
    started = time.monotonic()
    runtime = resolve_oci_runtime(settings.tool_sandbox_runtime)
    if runtime is None:
        return SandboxResult(None, "", "", "failed", 0, "sandbox_unavailable")

    name = f"hlg-tool-{uuid.uuid4().hex}"
    command = build_oci_command(
        runtime,
        settings.tool_sandbox_image,
        name,
        argv,
        memory_mb=settings.tool_sandbox_memory_mb,
        cpus=settings.tool_sandbox_cpus,
        pids_limit=settings.tool_sandbox_pids_limit,
        tmpfs_mb=settings.tool_sandbox_tmpfs_mb,
        stdin_enabled=stdin_data is not None,
    )
    with tempfile.TemporaryDirectory(prefix="hlg-oci-config-") as config_dir:
        env = _runtime_environment(config_dir)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except OSError:
            return SandboxResult(None, "", "", "failed", 0, "sandbox_unavailable")
        assert process.stdout is not None and process.stderr is not None
        if stdin_data is not None:
            assert process.stdin is not None
            try:
                process.stdin.write(stdin_data)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                # 容器可能未读取 stdin（命令提前退出），不是错误。
                pass
            process.stdin = None
        stdout_bytes, stderr_bytes = bytearray(), bytearray()
        exceeded = threading.Event()
        readers = [
            threading.Thread(
                target=_capture_stream, args=(process.stdout, stdout_bytes, exceeded), daemon=True
            ),
            threading.Thread(
                target=_capture_stream, args=(process.stderr, stderr_bytes, exceeded), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()

        stop_reason: str | None = None
        while process.poll() is None:
            if exceeded.is_set():
                stop_reason = "output_truncated"
                break
            if time.monotonic() - started >= timeout_seconds:
                stop_reason = "timeout"
                break
            time.sleep(0.01)
        if stop_reason:
            _kill_process_tree(process, env)
            _cleanup_container(runtime, name, env)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process, env)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_reason = "runtime_stuck"
                _cleanup_container(runtime, name, env)
        for reader in readers:
            reader.join(timeout=2)
        if stop_reason is None and exceeded.is_set():
            stop_reason = "output_truncated"

    duration = int((time.monotonic() - started) * 1000)
    stdout = bytes(stdout_bytes[:TOOL_MAX_STDOUT_BYTES]).decode("utf-8", errors="replace")
    stderr = bytes(stderr_bytes[:TOOL_MAX_STDOUT_BYTES]).decode("utf-8", errors="replace")
    if stop_reason == "timeout":
        log_event("warning", "tool.sandbox_timeout", "工具执行超时被终止")
        return SandboxResult(None, stdout, stderr, "timed_out", duration, "timeout")
    if stop_reason == "output_truncated":
        log_event("warning", "tool.sandbox_output_limit", "工具输出超过限制被终止")
        return SandboxResult(
            process.returncode, stdout, stderr, "limit_exceeded", duration, "output_truncated"
        )
    if stop_reason == "runtime_stuck":
        return SandboxResult(
            process.returncode, stdout, stderr, "failed", duration, "sandbox_runtime_error"
        )
    state = "succeeded" if process.returncode == 0 else "failed"
    if state == "succeeded":
        error_code = None
    elif process.returncode in {125, 126, 127}:
        error_code = "sandbox_runtime_error"
    else:
        error_code = "nonzero_exit"
    return SandboxResult(process.returncode, stdout, stderr, state, duration, error_code)

"""进程级工具沙箱执行器（M12，docs/ROADMAP.md M12 / docs/PRODUCT.md §6）。

隔离措施（平台差异文档化）：
- 独立子进程，工作目录为每次执行创建的临时目录（用后即删）；
- 超时上限（硬 kill）；输出单边 64 KiB 截断（防内存放大）；
- 环境变量清零（继承零环境——凭据不可能经环境泄漏给工具）；
- Linux 额外 resource.setrlimit 限制 CPU 秒数与地址空间；Windows 无
  等价机制，靠超时 + 输出截断 + 临时目录兜底（部署文档注明差异）；
- 网络隔离：沙箱不提供任何出网代理/凭据；严格网络阻断依赖部署层
  （如容器内运行整个网关并禁出网），进程级无法可移植地拦截 socket。

命令模板安全：占位符 {name} 只允许白名单 arguments 的字符串值替换，
渲染用 shlex 后拼 argv（不经 shell），参数值经 shlex.quote 防注入；
模板保存时校验占位符与 schema 一致。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from ...core.constants import (
    TOOL_MAX_STDOUT_BYTES,
)
from ...core.logging import log_event
from ...domain.errors import DomainError, DomainErrorCode


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    state: str  # succeeded / failed / timed_out / limit_exceeded
    duration_ms: int
    error_code: str | None = None


def _apply_posix_limits(cpu_seconds: int) -> None:
    """Linux only：限制子进程 CPU 时间与地址空间（Windows 跳过）。"""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        memory = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, ValueError, OSError):
        # Windows / 平台不支持：靠超时 + 输出截断 + 临时目录兜底。
        pass


def render_command(command_template: str, arguments: dict[str, Any]) -> list[str]:
    """模板 + 已校验参数 -> argv（不经 shell；参数值 shlex.quote 防注入）。"""
    rendered_parts: list[str] = []
    # 以空白拆分模板（模板由管理员维护、保存时已校验占位符集合）。
    for token in shlex.split(command_template, posix=False):
        part = token
        for key, value in arguments.items():
            placeholder = "{" + key + "}"
            if placeholder in part:
                if not isinstance(value, str):
                    raise DomainError(
                        DomainErrorCode.VALIDATION_FAILED,
                        f"参数 {key} 必须是字符串",
                        status_code=400,
                    )
                # 参数值整体 quote：即使含空格/引号/; 也是单参数。
                part = part.replace(placeholder, shlex.quote(value))
        rendered_parts.append(part)
    # posix=False 产生的带引号 token 再规范一次
    return [p.strip('"') if p.startswith('"') and p.endswith('"') else p for p in rendered_parts]


def run_sandboxed(
    argv: list[str],
    *,
    timeout_seconds: int,
) -> SandboxResult:
    """在临时目录中以清零环境运行 argv，返回截断后的结果。

    preexec_fn 仅 POSIX 可用（Windows 子进程不支持 fork 前钩子）。
    """
    import sys

    if not argv:
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "命令为空", status_code=400)
    is_posix = sys.platform != "win32"
    with tempfile.TemporaryDirectory(prefix="hlg-tool-") as workdir:
        started = time.monotonic()
        popen_kwargs: dict[str, Any] = {}
        if is_posix:
            popen_kwargs["preexec_fn"] = lambda: _apply_posix_limits(timeout_seconds)
        try:
            completed = subprocess.run(  # argv 经白名单+quote 渲染
                argv,
                cwd=workdir,
                env={},
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                **popen_kwargs,
            )
        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - started) * 1000)
            log_event(
                "warning",
                "tool.sandbox_timeout",
                "工具执行超时被终止",
                argv0=argv[0],
                timeout_seconds=timeout_seconds,
            )
            return SandboxResult(
                exit_code=None,
                stdout="",
                stderr="",
                state="timed_out",
                duration_ms=duration,
                error_code="timeout",
            )
        duration = int((time.monotonic() - started) * 1000)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        truncated = len(stdout) > TOOL_MAX_STDOUT_BYTES or len(stderr) > TOOL_MAX_STDOUT_BYTES
        stdout = stdout[:TOOL_MAX_STDOUT_BYTES]
        stderr = stderr[:TOOL_MAX_STDOUT_BYTES]
        state = "succeeded" if completed.returncode == 0 else "failed"
        if truncated:
            state = "limit_exceeded"
        return SandboxResult(
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            state=state,
            duration_ms=duration,
            error_code="output_truncated" if truncated else None,
        )


def validate_command_template(command_template: str, argument_names: list[str]) -> None:
    """保存白名单时校验：模板占位符必须是 schema 声明的参数名。"""
    import re

    placeholders = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", command_template))
    unknown = placeholders - set(argument_names)
    if unknown:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"命令模板包含未声明的参数占位符: {', '.join(sorted(unknown))}",
            status_code=400,
        )
    # 拒绝 shell 元字符（不经 shell 执行，但防管理员按 shell 语义误配置；
    # `;` 允许——python -c 等单行命令需要，argv 直传下无 shell 语义）。
    for forbidden in ("|", "`", "$(", "&&", "||", ">>", ">", "<"):
        if forbidden in command_template:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"命令模板不允许 shell 元字符: {forbidden}",
                status_code=400,
            )


def empty_env_hint() -> str:
    """文档性说明：沙箱进程环境为空。"""
    return f"env={os.environ and 'cleared'}"

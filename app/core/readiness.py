"""应用就绪状态：启动阶段写入，探针请求只读取内存状态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ReadinessState:
    """记录启动校验结果和后台协调器状态。

    `/readyz` 不能在每次探测时访问数据库或连接器；数据库和加密校验
    在 startup 中完成，协调器状态则通过已创建的 asyncio Task 动态判断。
    网关不执行调用方工具；就绪探针不检查工具执行状态。
    """

    startup: bool = False
    database: bool = False
    encryption: bool = False
    protocols: bool = False
    connector_registry: bool = False
    coordinator_tasks: tuple[asyncio.Task[Any], ...] = field(default_factory=tuple)

    def mark_bootstrap_complete(self) -> None:
        """Bootstrap 成功表示数据库、Schema 和加密 sentinel 均已通过。"""
        self.database = True
        self.encryption = True

    def mark_runtime_started(
        self,
        *,
        tasks: tuple[asyncio.Task[Any], ...],
        protocols_ready: bool,
        connector_registry_ready: bool,
    ) -> None:
        self.protocols = protocols_ready
        self.connector_registry = connector_registry_ready
        self.coordinator_tasks = tasks
        self.startup = True

    def reset(self) -> None:
        """应用关闭时撤销就绪状态，避免复用应用对象时泄露旧状态。"""
        self.startup = False
        self.database = False
        self.encryption = False
        self.protocols = False
        self.connector_registry = False
        self.coordinator_tasks = ()

    def snapshot(self, service: str) -> dict[str, Any]:
        coordinators_ready = self.connector_registry and bool(self.coordinator_tasks)
        coordinators_ready = coordinators_ready and all(
            not task.done() and not task.cancelled() for task in self.coordinator_tasks
        )
        checks = {
            "startup": self.startup,
            "database": self.database,
            "encryption": self.encryption,
            "protocols": self.protocols,
            "coordinators": coordinators_ready,
        }
        return {
            "status": "ready" if all(checks.values()) else "not_ready",
            "service": service,
            "checks": checks,
        }


def protocols_ready() -> bool:
    """确认三个外部协议的解析、响应和流式渲染入口都已装配。"""
    from ..protocols import anthropic, chat_completions, responses

    required = (
        (anthropic, ("parse_request", "render_response", "stream_events")),
        (chat_completions, ("parse_request", "render_response", "stream_frames")),
        (responses, ("parse_request", "render_response", "stream_events")),
    )
    return all(
        callable(getattr(module, name, None)) for module, names in required for name in names
    )

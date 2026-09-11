"""任务投递用例：把任务包投递到 IM 连接。

IM 投递失败不影响 Web 任务可见性（docs/ROADMAP.md M4）；本服务把
投递结果写入 outbox 与任务事件，绝不向准入调用方抛出异常。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..connectors.base import Connector, DeliveryEnvelope
from ..connectors.manager import ConnectionManager
from ..core.constants import IM_CONTENT_DETAIL_CHARS, IM_HINT_BAR_CHARS
from ..core.time import iso_utc
from ..domain.connections import ERROR_CONFIG
from ..domain.enums import ActorType, TaskEventType
from ..repositories.connections import ConnectionRepository
from ..repositories.models import ImConnection, RequestTask

# 使用 outbox 可靠投递的平台（docs/DATABASE.md §4.2）
OUTBOX_PLATFORMS = frozenset({"webhook", "http_poll", "websocket"})

# 展示用提示词摘要上限（字符）；超长保留尾部（Agent 提示词提问在末尾）。
_PROMPT_SUMMARY_CAP = 4000


def build_hint_bar(task: RequestTask, *, summary: str | None = None) -> str:
    """任务提示条（第一条消息）：任务定位 + 模型 + 提问摘要/LLM 总结。

    - summary 非空时用 LLM 总结，否则用 prompt 尾部摘要；
    - 超过 IM_HINT_BAR_CHARS 截断加省略号；
    - 末尾附带操作提示（#id 回复 / /page 全文）。
    """
    body = summary if summary else _tail_prompt(task)
    hint = f"[任务 {task.public_id}] 模型 {task.requested_model}\n{body}"
    operations = f"\n回复 #{task.public_id} <正文> 提交回复；/page 看全文"
    if len(hint) + len(operations) > IM_HINT_BAR_CHARS:
        keep = IM_HINT_BAR_CHARS - len(operations) - 1
        hint = hint[:keep].rstrip() + "…"
    return hint + operations


def build_content_bar(task: RequestTask) -> str | None:
    """内容条（第二条消息）：prompt 尾部摘录（IM_CONTENT_DETAIL_CHARS 内）。

    简版逻辑：prompt 尾部在预算内时返回全文摘录；超预算返回 None
    （只发提示条，用户用 /page 看全文），避免 IM 刷屏。
    """
    prompt = _tail_prompt(task, cap=IM_CONTENT_DETAIL_CHARS)
    if not prompt:
        return None
    if len(prompt) < IM_CONTENT_DETAIL_CHARS:
        return prompt
    return f"…（前面内容已省略，共 {len(prompt)} 字摘要）\n{prompt}"


def _tail_prompt(task: RequestTask, cap: int = _PROMPT_SUMMARY_CAP) -> str:
    """提示词尾部摘要（Agent 提问在末尾）；与 _extract_request_summary 同源。"""
    from .outbound_service import extract_full_prompt

    prompt = extract_full_prompt(task)
    if len(prompt) > cap:
        omitted = len(prompt) - cap
        prompt = f"…（前面 {omitted} 字已省略）\n{prompt[-cap:]}"
    return prompt


@dataclass
class DeliveryOutcome:
    connection_id: int
    platform: str
    delivered: bool
    error_code: str | None = None
    via_outbox: bool = False


class DeliveryService:
    def __init__(self, manager: ConnectionManager | None = None) -> None:
        self.repo = ConnectionRepository()
        self.manager = manager

    def _manager(self) -> ConnectionManager:
        from ..connectors import connection_manager as default_manager

        return self.manager or default_manager

    def deliver_task(
        self, session: Session, *, task: RequestTask, connection: ImConnection
    ) -> DeliveryOutcome:
        """投递任务包到指定连接；任何失败只记录，不抛出。

        两消息投递（docs/PRODUCT.md §6.4）：提示条必发，内容条按预算可选。
        LLM 总结在事件循环上下文并入后台投递链路（先出总结再建包推送），
        同步上下文内联生成；总结失败静默降级为尾部摘要。
        """
        want_summary = connection.llm_summary_enabled and connection.llm_config_id is not None
        summary: str | None = None
        connector = self._manager().get_instance(connection.id)
        envelope: DeliveryEnvelope
        if want_summary and connector is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                running_loop = False
            else:
                running_loop = True
            if running_loop:
                # 事件循环内：总结并入 after-commit 后台链路（只推送一次）。
                return self._deliver_async_with_summary(
                    session, connection=connection, task=task, connector=connector
                )
            from .llm_summary_service import summarize_task_prompt

            # 同步上下文（脚本/测试）：阻塞生成总结；失败静默降级为 None。
            summary = asyncio.run(summarize_task_prompt(session, connection=connection, task=task))
        envelope = self.build_envelope(task, summary=summary)
        payload = envelope.to_json()
        via_outbox = connection.platform in OUTBOX_PLATFORMS
        if via_outbox:
            self.repo.enqueue_outbox(
                session, connection_id=connection.id, task_id=task.id, payload=payload
            )
        if connector is None:
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code="connection_offline",
                via_outbox=via_outbox,
            )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            # 事件循环上下文（异步请求）：投递转为后台任务，任务包留在
            # outbox。必须在调用方事务提交后再推送，否则后台任务用新会话
            # 更新 outbox 时行尚不可见，确认会静默丢失（rowcount=0）。
            from sqlalchemy import event

            loop = asyncio.get_running_loop()
            pushed = False

            def _push_after_commit(_session: Session) -> None:
                nonlocal pushed
                if pushed:
                    return
                pushed = True
                loop.create_task(
                    self._async_push(connector, envelope, connection.id, task.id, via_outbox)
                )

            event.listen(session, "after_commit", _push_after_commit, once=True)
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code="delivery_scheduled",
                via_outbox=via_outbox,
            )
        try:
            _run(connector.deliver(envelope))
        except Exception as exc:  # noqa: BLE001  # 投递失败不影响任务
            error_code = getattr(exc, "code", ERROR_CONFIG)
            if via_outbox:
                self.repo.mark_outbox_failed(session, connection.id, task.id, str(error_code))
            self._add_event(session, task, connection, delivered=False, error_code=str(error_code))
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code=str(error_code),
                via_outbox=via_outbox,
            )
        if via_outbox:
            self.repo.mark_outbox_delivered(session, connection.id, task.id)
        self._add_event(session, task, connection, delivered=True)
        return DeliveryOutcome(
            connection_id=connection.id,
            platform=connection.platform,
            delivered=True,
            via_outbox=via_outbox,
        )

    async def _async_push(
        self,
        connector: Connector,
        envelope: DeliveryEnvelope,
        connection_id: int,
        task_id: int,
        via_outbox: bool,
    ) -> None:
        """异步上下文的后台投递：使用独立短会话更新 outbox 与事件。"""
        from ..core.db import SessionLocal
        from ..repositories.models import RequestTask as TaskRow

        delivered = False
        error_code: str | None = None
        try:
            await connector.deliver(envelope)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            error_code = str(getattr(exc, "code", ERROR_CONFIG))
        with SessionLocal() as session:
            task = session.get(TaskRow, task_id)
            connection = session.get(ImConnection, connection_id)
            if task is None or connection is None:
                return
            if via_outbox:
                if delivered:
                    self.repo.mark_outbox_delivered(session, connection_id, task_id)
                elif error_code:
                    self.repo.mark_outbox_failed(session, connection_id, task_id, error_code)
            self._add_event(session, task, connection, delivered=delivered, error_code=error_code)
            session.commit()

    # ------------------------------------------------------------------

    def build_envelope(self, task: RequestTask, *, summary: str | None = None) -> DeliveryEnvelope:
        prompt, tool_names = self._extract_request_summary(task)
        hint = build_hint_bar(task, summary=summary)
        content = build_content_bar(task)
        messages = [hint] if content is None else [hint, content]
        return DeliveryEnvelope(
            task_public_id=task.public_id,
            requested_model=task.requested_model,
            prompt_text=prompt,
            owner_user_id=task.owner_user_id,
            created_at=iso_utc(task.created_at) or "",
            has_tools=bool(tool_names),
            tool_names=tool_names,
            messages=messages,
        )

    # ------------------------------------------------------------------
    # LLM 总结（提示条摘要）
    # ------------------------------------------------------------------

    def _deliver_async_with_summary(
        self,
        session: Session,
        *,
        connection: ImConnection,
        task: RequestTask,
        connector: Connector,
    ) -> DeliveryOutcome:
        """事件循环上下文 + 开启总结的投递：after-commit 后先出总结再建包推送。

        与 _async_push 的差异只在推送前先跑总结（失败静默降级为尾部摘要），
        全程只推送一次；outbox 载荷在入会话时落最终版本。
        """
        from sqlalchemy import event

        loop = asyncio.get_running_loop()
        pushed = False

        def _push_after_commit(_session: Session) -> None:
            nonlocal pushed
            if pushed:
                return
            pushed = True
            loop.create_task(
                self._async_deliver_with_summary(
                    connection_id=connection.id, task_id=task.id, connector=connector
                )
            )

        event.listen(session, "after_commit", _push_after_commit, once=True)
        return DeliveryOutcome(
            connection_id=connection.id,
            platform=connection.platform,
            delivered=False,
            error_code="delivery_scheduled",
            via_outbox=connection.platform in OUTBOX_PLATFORMS,
        )

    async def _async_deliver_with_summary(
        self, *, connection_id: int, task_id: int, connector: Connector
    ) -> None:
        """后台链路：总结（可失败）→ 建包 → 入队/推送 → outbox 状态与事件。

        与 _async_push 同理用独立短会话（调用方会话在响应后可能被关闭）；
        总结失败时 build_envelope(summary=None) 自动退回尾部摘要。
        """
        from ..core.db import SessionLocal
        from .llm_summary_service import summarize_task_prompt

        with SessionLocal() as session:
            connection = session.get(ImConnection, connection_id)
            task = session.get(RequestTask, task_id)
            if connection is None or task is None:
                return
            summary = await summarize_task_prompt(session, connection=connection, task=task)
            envelope = self.build_envelope(task, summary=summary)
            via_outbox = connection.platform in OUTBOX_PLATFORMS
            if via_outbox:
                self.repo.enqueue_outbox(
                    session,
                    connection_id=connection_id,
                    task_id=task_id,
                    payload=envelope.to_json(),
                )
            delivered = False
            error_code: str | None = None
            try:
                await connector.deliver(envelope)
                delivered = True
            except Exception as exc:  # noqa: BLE001
                error_code = str(getattr(exc, "code", ERROR_CONFIG))
            if via_outbox:
                if delivered:
                    self.repo.mark_outbox_delivered(session, connection_id, task_id)
                elif error_code:
                    self.repo.mark_outbox_failed(session, connection_id, task_id, error_code)
            self._add_event(session, task, connection, delivered=delivered, error_code=error_code)
            session.commit()

    @staticmethod
    def _extract_request_summary(task: RequestTask) -> tuple[str, list[str]]:
        """从规范化请求提取展示用文本与工具名（防御式，M6 定义正式结构）。"""
        prompt = ""
        tool_names: list[str] = []
        try:
            normalized: dict[str, Any] = json.loads(task.normalized_request_json or "{}")
        except (ValueError, TypeError):
            normalized = {}
        messages = normalized.get("messages")
        if isinstance(messages, list) and messages:
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, str):
                        prompt = content
                    elif isinstance(content, list):
                        parts = [
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                        prompt = "\n".join(part for part in parts if part)
                    break
        if not prompt:
            prompt = normalized.get("input") if isinstance(normalized.get("input"), str) else ""
        tools = normalized.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    name = tool.get("name") or (tool.get("function", {}) or {}).get("name")
                    if isinstance(name, str):
                        tool_names.append(name)
        # Agent 工具（opencode 等）的提示词前面是海量系统上下文，真正的
        # 提问在末尾：超长时保留尾部，仅省略前缀。
        if len(prompt) > _PROMPT_SUMMARY_CAP:
            omitted = len(prompt) - _PROMPT_SUMMARY_CAP
            prompt = f"…（前面 {omitted} 字已省略）\n{prompt[-_PROMPT_SUMMARY_CAP:]}"
        return prompt, tool_names

    @staticmethod
    def _add_event(
        session: Session,
        task: RequestTask,
        connection: ImConnection,
        *,
        delivered: bool,
        error_code: str | None = None,
    ) -> None:
        from ..repositories.models import TaskEvent

        payload: dict[str, Any] = {
            "connection_id": connection.id,
            "platform": connection.platform,
            "delivered": delivered,
        }
        if error_code:
            payload["error_code"] = error_code
        session.add(
            TaskEvent(
                task_id=task.id,
                event_type=TaskEventType.DELIVERED,
                actor_type=ActorType.SYSTEM,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )


def _run(coro):  # pragma: no cover - 兼容旧调用方式，事件循环外运行协程
    """在事件循环外阻塞运行协程；事件循环内交给后台任务（返回 None）。"""
    try:
        asyncio.get_running_loop().create_task(coro)
        return None
    except RuntimeError:
        pass
    return asyncio.run(coro)

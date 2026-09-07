"""IM 内容外发用例：/page 分页全文与 /file 文件回复的统一出口。

与任务投递（delivery_service）不同，这里的消息不是"任务包"而是
"对进站命令的应答"，因此：

- 目标用户固定为连接的绑定用户（bound_external_user_id）；
- push 平台（wecom_aibot / wecom_ilink）走 connector.send_reply_text /
  connector.send_file 即时推送；
- outbox 平台（webhook / http_poll / websocket）写入 connector_outbox，
  payload 带 kind 标记，拉取方按 kind 分流处理；
- 所有失败只记日志，不影响进站命令事务（与任务投递同哲学）。

outbox 复用 connector_outbox 表：task_id 指向命令定位的任务（NOT NULL FK
约束需要），UNIQUE(connection_id, task_id) 冲突时把已有行重置回 PENDING
并覆盖 payload（命令可能重复发送，新内容应覆盖旧内容）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..connectors.base import Connector
from ..core.constants import (
    IM_FILE_FORMATS,
    IM_PAGE_CHARS,
)
from ..core.time import iso_utc, utc_now
from ..domain.connections import ERROR_DELIVERY
from ..domain.enums import OutboxDeliveryState
from ..repositories.models import ConnectorOutbox, RequestTask

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..repositories.models import ImConnection
    from .connection_service import ConnectionService

logger = logging.getLogger(__name__)

# outbox payload 的消息种类标记（任务包无 kind 字段，向后兼容）。
PAGE_KIND = "page"
FILE_KIND = "file"

# 临时文件大小上限：SDK 分片上传最多 100 片 × 512KB，取 30MB 留余量。
_FILE_MAX_BYTES = 30 * 1024 * 1024


def _split_pages(text: str, page_chars: int) -> list[str]:
    """按字符数分页；单页超长在最近的换行/空白处断开，避免截断语义单元。"""
    if not text:
        return [""]
    pages: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n"):
        while len(paragraph) > page_chars:
            # 段落本身超页：在预算内找最后的空白断点，找不到就硬切。
            budget = page_chars - current_len if current else page_chars
            if current and budget <= 0:
                pages.append("\n".join(current))
                current = []
                current_len = 0
                budget = page_chars
            cut = paragraph.rfind(" ", 0, budget)
            if cut <= 0:
                cut = budget
            chunk, paragraph = paragraph[:cut].rstrip(), paragraph[cut:].lstrip()
            current.append(chunk)
            current_len += len(chunk) + 1
            pages.append("\n".join(current))
            current = []
            current_len = 0
        if current_len + len(paragraph) + 1 > page_chars and current:
            pages.append("\n".join(current))
            current, current_len = [], 0
        current.append(paragraph)
        current_len += len(paragraph) + 1
    if current or not pages:
        pages.append("\n".join(current))
    return pages


def extract_full_chat(task: RequestTask) -> str:
    """从规范化请求提取完整聊天记录（user/assistant 所有消息）。

    供 /page 分页与 /file 文件回复共用；按角色标注后拼接为可读文本，
    无消息时回退到 extract_full_prompt 的提问全文。
    """
    try:
        normalized: dict[str, Any] = json.loads(task.normalized_request_json or "{}")
    except (ValueError, TypeError):
        normalized = {}
    messages = normalized.get("messages")
    if isinstance(messages, list) and messages:
        parts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "")
            text = _message_text(message.get("content"))
            if not text:
                continue
            label = "用户" if role == "user" else ("助手" if role == "assistant" else role)
            parts.append(f"{label}: {text}")
        if parts:
            return "\n\n".join(parts)
    # 无 messages 时回退提问全文（openai_responses input 场景）。
    return extract_full_prompt(task)


def _message_text(content: Any) -> str:
    """把消息 content（str 或 text 分块列表）还原为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def extract_full_prompt(task: RequestTask) -> str:
    """从规范化请求提取用户提问全文（不截断），供 /page 与 /file 共用。"""
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
                    return content
                if isinstance(content, list):
                    parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    return "\n".join(part for part in parts if part)
    if isinstance(normalized.get("input"), str):
        return normalized["input"]
    return ""


def send_page(
    service: ConnectionService,
    session: Session,
    *,
    row: ImConnection,
    task: RequestTask,
    page: int,
) -> None:
    """/page [n]：把聊天记录第 n 页发回绑定用户（每页 500 字）。

    先展示"共多少页/当前第几页"；开启 LLM 压缩时先压缩到 500 字再分页。
    页码越界回第 1 页。
    """
    chat = extract_full_chat(task)
    chat = _maybe_compress_chat(session, row=row, task=task, chat=chat)
    pages = _split_pages(chat, IM_PAGE_CHARS)
    total = len(pages)
    if page < 1 or page > total:
        page = 1
    content = pages[page - 1]
    header = f"[{task.public_id}] 共 {total} 页，当前第 {page} 页"
    if total > 1 and page < total:
        header += f"（回复 /page {page + 1} 看下一页）"
    text = f"{header}\n\n{content}"
    _dispatch_outbound(session, row=row, kind=PAGE_KIND, task=task, text=text)


def send_file(
    service: ConnectionService,
    session: Session,
    *,
    row: ImConnection,
    task: RequestTask,
    fmt: str,
) -> None:
    """/file [md|txt]：把整个聊天记录打包成文件发回绑定用户（默认 txt）。"""
    if fmt not in IM_FILE_FORMATS:
        fmt = "txt"
    chat = extract_full_chat(task)
    chat = _maybe_compress_chat(session, row=row, task=task, chat=chat)
    header = (
        f"任务 {task.public_id}\n模型 {task.requested_model}\n"
        f"创建时间 {iso_utc(task.created_at) or ''}\n\n"
    )
    body = f"# {task.public_id}\n\n{chat}" if fmt == "md" else chat
    content = header + body
    filename = f"{task.public_id}.{fmt}"
    _dispatch_outbound(session, row=row, kind=FILE_KIND, task=task, text=content, filename=filename)


# ---------------------------------------------------------------------------
# 统一出口：push 直推 / outbox 入队
# ---------------------------------------------------------------------------


def _maybe_compress_chat(
    session: Session, *, row: ImConnection, task: RequestTask, chat: str
) -> str:
    """连接开启 LLM 压缩且绑定配置时，把聊天记录压缩到 500 字；否则返回原文。

    同步上下文（webhook 入站为同步端点）用 asyncio.run 等待压缩结果；
    事件循环内跳过压缩（避免阻塞循环），失败一律静默降级为原文。
    """
    if not row.llm_summary_enabled or row.llm_config_id is None:
        return chat
    import asyncio

    from .llm_summary_service import compress_chat

    async def _run() -> str | None:
        return await compress_chat(session, connection=row, task=task, chat=chat)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(_run())
    else:
        return chat
    return result if result else chat


def _dispatch_outbound(
    session: Session,
    *,
    row: ImConnection,
    kind: str,
    task: RequestTask,
    text: str,
    filename: str | None = None,
) -> None:
    target = row.bound_external_user_id or ""
    connector = _get_connector(row.id)
    if row.platform in {"webhook", "http_poll", "websocket"}:
        _enqueue_outbound_payload(
            session, row=row, task=task, kind=kind, text=text, filename=filename
        )
        # 有在线会话时尽力即时推送（outbox 保留给拉取方兜底）。
        if connector is not None:
            _push_best_effort(
                connector,
                target=target,
                text=text if kind == PAGE_KIND else None,
                filename=filename,
                file_content=None if kind == PAGE_KIND else text,
            )
        return
    # push 平台：直接推送，失败只记日志。
    if connector is None:
        logger.warning("outbound skipped: connection %s offline", row.id)
        return
    if not target:
        logger.warning("outbound skipped: connection %s unbound", row.id)
        return
    _push_best_effort(
        connector,
        target=target,
        text=text if filename is None else None,
        filename=filename,
        file_content=text if filename is not None else None,
    )


def _push_best_effort(
    connector: Connector,
    *,
    target: str,
    text: str | None,
    filename: str | None,
    file_content: str | None,
) -> None:
    import asyncio

    async def _run() -> None:
        try:
            if text is not None:
                await connector.send_reply_text(target, text)
            if filename and file_content is not None:
                await connector.send_file(target, filename, file_content)
        except Exception:  # 外发失败不影响进站命令事务
            logger.exception("outbound push failed via %s", connector.platform)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run())
    else:
        asyncio.get_running_loop().create_task(_run())


def _get_connector(connection_id: int) -> Connector | None:
    from ..connectors import connection_manager as manager

    return manager.get_instance(connection_id)


def _enqueue_outbound_payload(
    session: Session,
    *,
    row: ImConnection,
    task: RequestTask,
    kind: str,
    text: str,
    filename: str | None,
) -> None:
    """把外发内容写入 connector_outbox（复用任务包通路，带 kind 标记）。

    UNIQUE(connection_id, task_id) 冲突时重置已有行为 PENDING 并覆盖 payload。
    """
    payload = {
        "kind": kind,
        "task_id": task.public_id,
        "created_at": iso_utc(utc_now()) or "",
    }
    if kind == PAGE_KIND:
        payload["text"] = text
    else:
        payload["filename"] = filename
        payload["content"] = text
    existing = session.execute(
        select(ConnectorOutbox).where(
            ConnectorOutbox.connection_id == row.id,
            ConnectorOutbox.task_id == task.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.payload_json = json.dumps(payload, ensure_ascii=False)
        existing.delivery_state = OutboxDeliveryState.PENDING
        existing.available_at = utc_now()
        existing.last_error_code = None
        return
    session.add(
        ConnectorOutbox(
            connection_id=row.id,
            task_id=task.id,
            payload_json=json.dumps(payload, ensure_ascii=False),
            available_at=utc_now(),
        )
    )


def describe_page_error(error: str) -> str:  # pragma: no cover - 供连接器错误归类复用
    return f"{ERROR_DELIVERY}: {error}"

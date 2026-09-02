"""高频运行数据保留：按周删除七天前的日志与会话记录。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_

from ..core.constants import DATA_RETENTION_DAYS, DATA_RETENTION_INTERVAL_SECONDS
from ..core.logging import bind_trace_id, log_event, new_trace_id, reset_request_id
from ..core.time import utc_now
from ..domain.enums import OutboxDeliveryState
from ..repositories.models import (
    AppLog,
    AssistantMessage,
    AssistantSession,
    AuditLog,
    AuthSession,
    ConnectorOutbox,
    InboundReceipt,
    TaskEvent,
    TaskInboxState,
    ToolExecution,
)

logger = logging.getLogger(__name__)


def _deleted(result: Any) -> int:
    """将 SQLAlchemy 不同方言的 rowcount 统一成可记录的整数。"""
    return max(0, int(result.rowcount or 0))


class DataRetentionService:
    """清理不参与业务历史的高频数据，不删除请求任务本身。"""

    def cleanup_once(self, *, now: datetime | None = None) -> dict[str, int]:
        from ..core.db import SessionLocal

        current = now or utc_now()
        cutoff = current - timedelta(days=DATA_RETENTION_DAYS)
        counts: dict[str, int] = {}

        with SessionLocal() as session:
            counts["app_logs"] = _deleted(
                session.execute(delete(AppLog).where(AppLog.created_at < cutoff))
            )
            counts["audit_logs"] = _deleted(
                session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
            )

            # 先删消息，再删闲置会话，避免依赖数据库级联开关。
            counts["assistant_messages"] = _deleted(
                session.execute(
                    delete(AssistantMessage).where(AssistantMessage.created_at < cutoff)
                )
            )
            counts["assistant_sessions"] = _deleted(
                session.execute(
                    delete(AssistantSession).where(
                        or_(
                            AssistantSession.last_message_at < cutoff,
                            AssistantSession.last_message_at.is_(None),
                        ),
                        AssistantSession.updated_at < cutoff,
                    )
                )
            )

            # 只清理已过期或已撤销的旧登录态，不影响仍有效的会话。
            counts["auth_sessions"] = _deleted(
                session.execute(
                    delete(AuthSession).where(
                        AuthSession.created_at < cutoff,
                        or_(
                            AuthSession.revoked_at.is_not(None),
                            AuthSession.expires_at <= current,
                        ),
                    )
                )
            )

            # 任务本身与正式草稿保留；以下记录只用于短期运行态和过程展示。
            counts["task_events"] = _deleted(
                session.execute(delete(TaskEvent).where(TaskEvent.created_at < cutoff))
            )
            counts["task_inbox_states"] = _deleted(
                session.execute(delete(TaskInboxState).where(TaskInboxState.updated_at < cutoff))
            )
            counts["tool_executions"] = _deleted(
                session.execute(delete(ToolExecution).where(ToolExecution.created_at < cutoff))
            )
            counts["inbound_receipts"] = _deleted(
                session.execute(delete(InboundReceipt).where(InboundReceipt.created_at < cutoff))
            )
            counts["connector_outbox"] = _deleted(
                session.execute(
                    delete(ConnectorOutbox).where(
                        ConnectorOutbox.updated_at < cutoff,
                        ConnectorOutbox.delivery_state.in_(
                            [OutboxDeliveryState.ACKED, OutboxDeliveryState.FAILED]
                        ),
                    )
                )
            )
            session.commit()

        return counts

    def _cleanup(self) -> dict[str, int]:
        token = bind_trace_id(new_trace_id())
        try:
            counts = self.cleanup_once()
            log_event(
                "info",
                "data_retention.cleaned",
                "高频数据清理完成",
                retention_days=DATA_RETENTION_DAYS,
                removed=sum(counts.values()),
                **counts,
            )
            return counts
        finally:
            reset_request_id(token)

    async def run(self) -> None:
        """启动时先清理一次，之后每七天执行一轮。"""
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._cleanup)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("data retention cycle failed")
                log_event("error", "data_retention.cycle_failed", "高频数据清理失败")
            await asyncio.sleep(DATA_RETENTION_INTERVAL_SECONDS)


data_retention = DataRetentionService()

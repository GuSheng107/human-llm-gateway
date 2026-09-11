"""单实例启动恢复和关闭：旧 HTTP 调用方已消失，取消活动任务并释放名额。"""

from __future__ import annotations

from sqlalchemy import select

from ..domain.tasks import TERMINAL_STATES
from ..repositories.models import RequestTask
from .inference_service import InferenceService


def cancel_active_tasks(reason: str) -> int:
    from ..core.db import SessionLocal

    service = InferenceService()
    cancelled = 0
    while True:
        with SessionLocal() as session:
            ids = list(
                session.scalars(
                    select(RequestTask.id)
                    .where(
                        RequestTask.state.not_in(list(TERMINAL_STATES)),
                        RequestTask.slot_released_at.is_(None),
                    )
                    .limit(200)
                )
            )
            if not ids:
                return cancelled
            for task_id in ids:
                cancelled += service.cancel_caller_disconnected(session, task_id, reason=reason)
            session.commit()

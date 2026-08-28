"""任务仓库：状态推进、首个回复、名额释放、fallback 声明、断开取消。

所有竞争都由条件 UPDATE 原子裁决（见 docs/DATABASE.md §10），不依赖进程锁。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import TaskState
from ..domain.tasks import TERMINAL_STATES
from .models import RequestTask


def _now() -> datetime:
    return utc_now()


def _terminal_values(state: TaskState) -> dict:
    return {
        "state": state,
        "slot_released_at": _now(),
        "completed_at": _now(),
        "updated_at": _now(),
    }


class TaskRepository:
    def get(self, session: Session, task_id: int) -> RequestTask | None:
        return session.get(RequestTask, task_id)

    def add(self, session: Session, task: RequestTask) -> RequestTask:
        session.add(task)
        return task

    def first_reply_wins(
        self,
        session: Session,
        *,
        task_id: int,
        owner_user_id: int,
        expected_version: int,
        response_payload_json: str,
    ) -> bool:
        """首个回复获胜：只有 waiting_human + 版本匹配才推进到 response_ready。"""
        result = session.execute(
            update(RequestTask)
            .where(
                RequestTask.id == task_id,
                RequestTask.owner_user_id == owner_user_id,
                RequestTask.state == TaskState.WAITING_HUMAN,
                RequestTask.version == expected_version,
            )
            .values(
                state=TaskState.RESPONSE_READY,
                response_payload_json=response_payload_json,
                version=RequestTask.version + 1,
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

    def claim_fallback(self, session: Session, task_id: int) -> bool:
        """fallback 唯一声明：只有 waiting_human 才能原子推进到 forwarding_llm。"""
        result = session.execute(
            update(RequestTask)
            .where(
                RequestTask.id == task_id,
                RequestTask.state == TaskState.WAITING_HUMAN,
            )
            .values(
                state=TaskState.FORWARDING_LLM,
                version=RequestTask.version + 1,
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

    def advance_state(self, session: Session, task_id: int, state: TaskState) -> int:
        result = session.execute(
            update(RequestTask)
            .where(RequestTask.id == task_id)
            .values(state=state, version=RequestTask.version + 1, updated_at=_now())
        )
        return result.rowcount

    def release_slot_to_terminal(self, session: Session, task_id: int, state: TaskState) -> bool:
        """推进到终态并幂等释放名额；影响 1 行才需扣减用户计数。"""
        if state not in TERMINAL_STATES:
            raise ValueError(f"非终态不能走名额释放路径: {state}")
        result = session.execute(
            update(RequestTask)
            .where(RequestTask.id == task_id, RequestTask.slot_released_at.is_(None))
            .values(**_terminal_values(state))
        )
        return result.rowcount == 1

    def cancel_caller_disconnected(self, session: Session, task_id: int) -> bool:
        """外部调用方断开取消（首个合法转换获胜）。"""
        result = session.execute(
            update(RequestTask)
            .where(
                RequestTask.id == task_id,
                RequestTask.state.not_in(
                    [
                        TaskState.COMPLETED,
                        TaskState.FAILED,
                        TaskState.TIMED_OUT,
                        TaskState.CANCELLED,
                    ]
                ),
                RequestTask.slot_released_at.is_(None),
            )
            .values(
                **_terminal_values(TaskState.CANCELLED),
                cancel_reason_code="caller_disconnected",
            )
        )
        return result.rowcount == 1

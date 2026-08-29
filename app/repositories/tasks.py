"""任务仓库：状态推进、首个回复、名额释放、fallback 声明、断开取消、
任务列表、事件分页与草稿 CRUD（docs/DATABASE.md §10）。

所有竞争都由条件 UPDATE 原子裁决，不依赖进程锁。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import DraftSource, DraftState, TaskState
from ..domain.tasks import TERMINAL_STATES
from .models import RequestTask, TaskDraft, TaskEvent


def _now() -> datetime:
    return utc_now()


def _terminal_values(state: TaskState) -> dict:
    return {
        "state": state,
        "slot_released_at": _now(),
        "completed_at": _now(),
        "version": RequestTask.version + 1,
        "updated_at": _now(),
    }


class TaskRepository:
    def get(self, session: Session, task_id: int) -> RequestTask | None:
        return session.get(RequestTask, task_id)

    def get_previous_public_id(self, session: Session, task: RequestTask) -> str | None:
        """取前置任务的 public_id（详情视图用，FK 保护下不会 None）。"""
        if task.previous_task_id is None:
            return None
        prev = session.get(RequestTask, task.previous_task_id)
        return prev.public_id if prev else None

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

    def accept_forward_reply(
        self,
        session: Session,
        *,
        task_id: int,
        owner_user_id: int,
        expected_version: int,
        response_payload_json: str,
    ) -> bool:
        """LLM 转发结果接受：forwarding_llm + 版本匹配才推进到 response_ready。

        与 first_reply_wins 对称：人工回复从 WAITING_HUMAN 接受，转发结果从
        FORWARDING_LLM 接受；两个入口互斥（claim_fallback 原子切换归属）。
        """
        result = session.execute(
            update(RequestTask)
            .where(
                RequestTask.id == task_id,
                RequestTask.owner_user_id == owner_user_id,
                RequestTask.state == TaskState.FORWARDING_LLM,
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

    def count_active_for_user(self, session: Session, user_id: int) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(RequestTask)
                .where(
                    RequestTask.owner_user_id == user_id,
                    RequestTask.slot_released_at.is_(None),
                    RequestTask.state.not_in(list(TERMINAL_STATES)),
                )
            )
            or 0
        )

    def cancel_all_for_user(self, session: Session, user_id: int, reason: str) -> int:
        """禁用用户时批量取消全部活动任务，并逐行语义等价地释放名额。"""
        result = session.execute(
            update(RequestTask)
            .where(
                RequestTask.owner_user_id == user_id,
                RequestTask.slot_released_at.is_(None),
                RequestTask.state.not_in(list(TERMINAL_STATES)),
            )
            .values(
                **_terminal_values(TaskState.CANCELLED),
                cancel_reason_code=reason,
            )
        )
        return result.rowcount

    # ------------------------------------------------------------------
    # 任务列表与事件时间线（M6-B）
    # ------------------------------------------------------------------

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        owner_user_id: int | None = None,
        search: str | None = None,
        state: TaskState | None = None,
    ) -> tuple[list[RequestTask], int]:
        """分页查询任务；普通用户按 owner 过滤，管理员传 None 看全部。"""
        filters: list = []
        if owner_user_id is not None:
            filters.append(RequestTask.owner_user_id == owner_user_id)
        if state is not None:
            filters.append(RequestTask.state == state)
        if search:
            term = search.strip()
            filters.append(
                or_(
                    RequestTask.public_id.ilike(f"%{term}%"),
                    RequestTask.requested_model.ilike(f"%{term}%"),
                    RequestTask.api_key_prefix_snapshot.ilike(f"%{term}%"),
                )
            )
        total = session.scalar(select(func.count()).select_from(RequestTask).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(RequestTask)
                .where(*filters)
                .order_by(RequestTask.created_at.desc(), RequestTask.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def list_events(
        self,
        session: Session,
        *,
        task_id: int,
        page: int,
        page_size: int,
    ) -> tuple[list[TaskEvent], int]:
        """分页查询任务事件（时间线，按 id 升序）。"""
        total = (
            session.scalar(
                select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)
            )
            or 0
        )
        rows = list(
            session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def get_by_public_id(
        self, session: Session, *, owner_user_id: int, public_id: str
    ) -> RequestTask | None:
        return session.execute(
            select(RequestTask).where(
                RequestTask.public_id == public_id,
                RequestTask.owner_user_id == owner_user_id,
            )
        ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # 草稿 CRUD（M6-B）
    # ------------------------------------------------------------------

    def get_draft(self, session: Session, draft_id: int) -> TaskDraft | None:
        return session.get(TaskDraft, draft_id)

    def get_active_draft(self, session: Session, *, task_id: int) -> TaskDraft | None:
        """返回任务的最新未提交草稿（state=EDITING），用于编辑器恢复。"""
        return session.execute(
            select(TaskDraft)
            .where(TaskDraft.task_id == task_id, TaskDraft.state == DraftState.EDITING)
            .order_by(TaskDraft.updated_at.desc(), TaskDraft.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def list_drafts(self, session: Session, *, task_id: int) -> list[TaskDraft]:
        return list(
            session.scalars(
                select(TaskDraft)
                .where(TaskDraft.task_id == task_id)
                .order_by(TaskDraft.updated_at.desc(), TaskDraft.id.desc())
            )
        )

    def create_draft(
        self,
        session: Session,
        *,
        task_id: int,
        owner_user_id: int,
        source: DraftSource,
        reasoning_text: str | None,
        tool_calls_json: str,
        final_text: str | None,
    ) -> TaskDraft:
        row = TaskDraft(
            task_id=task_id,
            owner_user_id=owner_user_id,
            source=source,
            state=DraftState.EDITING,
            reasoning_text=reasoning_text,
            tool_calls_json=tool_calls_json,
            final_text=final_text,
        )
        session.add(row)
        return row

    def mark_draft_state(self, session: Session, *, draft_id: int, state: DraftState) -> bool:
        result = session.execute(
            update(TaskDraft)
            .where(TaskDraft.id == draft_id, TaskDraft.state == DraftState.EDITING)
            .values(state=state, version=TaskDraft.version + 1, updated_at=_now())
        )
        return result.rowcount == 1

    def delete_draft(self, session: Session, *, draft_id: int) -> bool:
        row = session.get(TaskDraft, draft_id)
        if row is None or row.state is not DraftState.EDITING:
            return False
        session.delete(row)
        return True

    @staticmethod
    def draft_payload(row: TaskDraft) -> dict:
        """把 TaskDraft 行还原为 ReplyDraft 兼容的 dict。"""
        try:
            tool_calls = json.loads(row.tool_calls_json or "[]")
        except (ValueError, TypeError):
            tool_calls = []
        return {
            "reasoning": row.reasoning_text,
            "tool_calls": tool_calls,
            "final_text": row.final_text,
        }

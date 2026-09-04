"""任务工作台用例（docs/API_CONTRACT.md §9）：任务详情、草稿保存恢复与原子提交。

提交语义与 IM 路径（ConnectionService._submit_task_reply）对称：调用
TaskRepository.first_reply_wins 做首个有效提交裁决，晚到回复只记录事件
与审计，绝不覆盖已接受结果。管理员对草稿与回复写接口只读。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from ..core.db import begin_immediate_if_sqlite
from ..core.logging import get_request_id, log_event
from ..domain.enums import (
    ActorType,
    AuditAction,
    DraftSource,
    DraftState,
    TaskEventType,
    TaskState,
    UserRole,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft, ReplyToolCall
from ..protocols.normalized import declared_tool_names
from ..repositories.catalog import FakeModelRepository
from ..repositories.models import FakeModel, RequestTask, TaskDraft, TaskEvent, User
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository


def assert_reply_tool_names_declared(
    task: RequestTask, tool_calls: Sequence[ReplyToolCall]
) -> None:
    """人工写回的 tool_call 名称必须命中调用方在请求中声明的工具。

    与真实 LLM 对齐：网关只伪造输出、不执行，也不允许凭空捏造未声明
    的工具名回传给调用方；未命中时拒绝 400，避免协议解析失败或诱导
    调用方执行未知工具。
    """
    if not tool_calls:
        return
    try:
        normalized: dict[str, Any] = json.loads(task.normalized_request_json or "{}")
    except (ValueError, TypeError):
        normalized = {}
    declared = set(declared_tool_names(normalized))
    unknown = sorted({call.name for call in tool_calls if call.name not in declared})
    if unknown:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"工具 {'、'.join(unknown)} 不在调用方声明的工具内，人工回复的 tool_call 只能引用请求声明的工具",
            status_code=400,
        )


class TaskService:
    def __init__(self) -> None:
        self.repo = TaskRepository()
        self.catalog = FakeModelRepository()
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_tasks(
        self,
        session: Session,
        *,
        user: User,
        page: int,
        page_size: int,
        search: str | None = None,
        state: TaskState | None = None,
        states: list[TaskState] | None = None,
    ) -> tuple[list[RequestTask], int]:
        owner_filter = None if user.role is UserRole.ADMIN else user.id
        return self.repo.list_page(
            session,
            page=page,
            page_size=page_size,
            owner_user_id=owner_filter,
            search=search,
            state=state,
            states=states,
        )

    def get_owned_task(self, session: Session, task_id: int, user: User) -> RequestTask:
        task = self.repo.get(session, task_id)
        if task is None or (user.role is not UserRole.ADMIN and task.owner_user_id != user.id):
            raise DomainError(DomainErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        return task

    def list_events(
        self, session: Session, *, task: RequestTask, page: int, page_size: int
    ) -> tuple[list[TaskEvent], int]:
        return self.repo.list_events(session, task_id=task.id, page=page, page_size=page_size)

    def active_draft(self, session: Session, *, task: RequestTask) -> TaskDraft | None:
        return self.repo.get_active_draft(session, task_id=task.id)

    def drafts(self, session: Session, *, task: RequestTask) -> list[TaskDraft]:
        return self.repo.list_drafts(session, task_id=task.id)

    def result_draft(self, task: RequestTask) -> ReplyDraft | None:
        if not task.response_payload_json:
            return None
        try:
            return ReplyDraft.model_validate_json(task.response_payload_json)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def fake_model_name(session: Session, task: RequestTask) -> str:
        if task.fake_model_id is None:
            return task.requested_model
        model = session.get(FakeModel, task.fake_model_id)
        return model.model_id if model else task.requested_model

    # ------------------------------------------------------------------
    # 草稿保存与恢复
    # ------------------------------------------------------------------

    def save_draft(
        self,
        session: Session,
        *,
        task: RequestTask,
        owner: User,
        draft: ReplyDraft,
    ) -> TaskDraft:
        """新建或更新活动草稿（upsert 语义：已有 EDITING 则覆盖字段）。"""
        self._assert_writable(task, owner)
        assert_reply_tool_names_declared(task, draft.tool_calls)
        begin_immediate_if_sqlite(session)
        row = self.repo.get_active_draft(session, task_id=task.id)
        payload = self._draft_payload(draft)
        if row is None:
            row = self.repo.create_draft(
                session,
                task_id=task.id,
                owner_user_id=owner.id,
                source=DraftSource.MANUAL,
                reasoning_text=payload["reasoning"],
                tool_calls_json=payload["tool_calls_json"],
                final_text=payload["final_text"],
            )
        else:
            row.reasoning_text = payload["reasoning"]
            row.tool_calls_json = payload["tool_calls_json"]
            row.final_text = payload["final_text"]
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.TASK_REPLY_SUBMITTED,
            resource_type="task_draft",
            resource_id=str(row.id),
            actor_user_id=owner.id,
            owner_user_id=task.owner_user_id,
            metadata={"fields": ["reasoning", "tool_calls", "final_text"], "action": "draft_saved"},
        )
        return row

    def update_draft(
        self,
        session: Session,
        *,
        task: RequestTask,
        owner: User,
        draft_id: int,
        draft: ReplyDraft,
        expected_version: int,
    ) -> TaskDraft:
        """乐观锁更新：expected_version 必填且必须匹配当前 version。

        不匹配返回 409（public_code=draft_version_conflict），由前端弹
        "刷新 / 强制覆盖"。不再兼容不带 expected_version 的旧语义。
        """
        self._assert_writable(task, owner)
        assert_reply_tool_names_declared(task, draft.tool_calls)
        begin_immediate_if_sqlite(session)
        row = self.repo.get_draft(session, draft_id)
        if row is None or row.task_id != task.id or row.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "草稿不存在", status_code=404)
        if row.state is not DraftState.EDITING:
            raise DomainError(DomainErrorCode.CONFLICT, "草稿已提交，不能修改", status_code=409)
        payload = self._draft_payload(draft)
        ok = self.repo.update_draft_fields(
            session,
            draft_id=draft_id,
            expected_version=expected_version,
            reasoning_text=payload["reasoning"],
            tool_calls_json=payload["tool_calls_json"],
            final_text=payload["final_text"],
        )
        if not ok:
            current = self.repo.get_draft(session, draft_id)
            raise DomainError(
                DomainErrorCode.CONFLICT,
                f"草稿版本不匹配（当前 version={current.version if current else '已删除'}），请刷新后重试",
                status_code=409,
                public_code="draft_version_conflict",
            )
        session.flush()
        session.refresh(row)
        return row

    def delete_draft(
        self, session: Session, *, task: RequestTask, owner: User, draft_id: int
    ) -> None:
        self._assert_writable(task, owner)
        begin_immediate_if_sqlite(session)
        row = self.repo.get_draft(session, draft_id)
        if row is None or row.task_id != task.id or row.owner_user_id != owner.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "草稿不存在", status_code=404)
        if row.state is not DraftState.EDITING:
            raise DomainError(DomainErrorCode.CONFLICT, "草稿已提交，不能删除", status_code=409)
        session.delete(row)

    # ------------------------------------------------------------------
    # 原子提交
    # ------------------------------------------------------------------

    def submit_reply(
        self,
        session: Session,
        *,
        task: RequestTask,
        owner: User,
        draft: ReplyDraft,
        source_draft_id: int | None = None,
    ) -> bool:
        """首个有效提交获胜；晚到返回 False（调用方需记录晚到事件后抛 409）。"""
        self._assert_writable(task, owner)
        assert_reply_tool_names_declared(task, draft.tool_calls)
        begin_immediate_if_sqlite(session)
        accepted = self.repo.first_reply_wins(
            session,
            task_id=task.id,
            owner_user_id=task.owner_user_id,
            expected_version=task.version,
            response_payload_json=draft.model_dump_json(exclude_none=True),
        )
        if accepted:
            self._add_event(
                session,
                task_id=task.id,
                event_type=TaskEventType.REPLY_SUBMITTED,
                actor_type=ActorType.USER,
                actor_user_id=owner.id,
                payload={"source": "web", "source_draft_id": source_draft_id},
            )
            if source_draft_id is not None:
                self.repo.mark_draft_state(
                    session, draft_id=source_draft_id, state=DraftState.SUBMITTED
                )
            self.audit.add(
                session,
                action=AuditAction.TASK_REPLY_SUBMITTED,
                resource_type="request_task",
                resource_id=str(task.id),
                actor_user_id=owner.id,
                owner_user_id=task.owner_user_id,
                metadata={"fields": ["response_payload"], "source": "web"},
            )
            log_event(
                "info",
                "task.reply_submitted",
                "收到首个有效回复",
                task_id=task.id,
                owner_user_id=task.owner_user_id,
                source_draft_id=source_draft_id,
            )
            return True
        self._add_event(
            session,
            task_id=task.id,
            event_type=TaskEventType.REPLY_REJECTED_LATE,
            actor_type=ActorType.USER,
            actor_user_id=owner.id,
            payload={"source": "web", "source_draft_id": source_draft_id},
        )
        self.audit.add(
            session,
            action=AuditAction.TASK_REPLY_SUBMITTED,
            resource_type="request_task",
            resource_id=str(task.id),
            actor_user_id=owner.id,
            owner_user_id=task.owner_user_id,
            metadata={"fields": ["response_payload"], "source": "web", "result": "late"},
        )
        log_event(
            "warning",
            "task.reply_rejected_late",
            "晚到回复被拒",
            task_id=task.id,
            owner_user_id=task.owner_user_id,
            source_draft_id=source_draft_id,
        )
        return False

    # ------------------------------------------------------------------
    # 内部

    @staticmethod
    def _assert_owner(task: RequestTask, user: User) -> None:
        if user.role is not UserRole.ADMIN and task.owner_user_id != user.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "任务不存在", status_code=404)

    @staticmethod
    def _assert_writable(task: RequestTask, owner: User) -> None:
        """写接口统一前置：归属校验 + 管理员禁写 + 用户仍启用 + 任务仍可编辑。

        AGENTS.md §1：禁用用户时必须立即终止活动任务并释放名额；本校验
        兜底用户在取消尚未生效时通过已发出的会话/凭据继续写回复。
        """
        TaskService._assert_owner(task, owner)
        if owner.role is UserRole.ADMIN:
            raise DomainError(
                DomainErrorCode.FORBIDDEN, "管理员不能编辑草稿或提交回复", status_code=403
            )
        if not owner.is_active:
            raise DomainError(
                DomainErrorCode.FORBIDDEN,
                "账户已停用，无法继续编辑或回复",
                status_code=403,
            )
        TaskService._assert_editable(task)

    @staticmethod
    def _assert_editable(task: RequestTask) -> None:
        if task.state is not TaskState.WAITING_HUMAN:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "任务已结束，不能编辑或提交",
                status_code=409,
                public_code="task_already_resolved",
            )

    @staticmethod
    def _draft_payload(draft: ReplyDraft) -> dict[str, Any]:
        tool_calls = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in draft.tool_calls
        ]
        return {
            "reasoning": (draft.reasoning.strip() or None) if draft.reasoning else None,
            "tool_calls_json": json.dumps(tool_calls, ensure_ascii=False),
            "final_text": (draft.final_text.strip() or None) if draft.final_text else None,
        }

    @staticmethod
    def _add_event(
        session: Session,
        *,
        task_id: int,
        event_type: TaskEventType,
        actor_type: ActorType,
        actor_user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            TaskEvent(
                task_id=task_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
                request_id=get_request_id(),
            )
        )


def draft_from_row(row: TaskDraft) -> ReplyDraft:
    """把草稿行还原为 ReplyDraft（Web 编辑器恢复用）。"""
    return ReplyDraft.model_validate(TaskRepository.draft_payload(row))

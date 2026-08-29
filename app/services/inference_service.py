"""推理任务编排用例（docs/API_CONTRACT.md §12）。

准入顺序：解析 -> Key 鉴权（API 层）-> 有效模型校验 -> 原子名额占用 ->
完整落库 -> 策略分发；任何失败不投递、不超名额。M6 阶段所有策略均由
人工或 IM 给出回复（LLM 转发在 M7 落地），任务统一进入 waiting_human。
"""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.constants import MAX_CONTEXT_CHAIN_DEPTH
from ..core.time import utc_now
from ..domain.enums import (
    ActorType,
    DeliveryMode,
    InferenceProtocol,
    ReplyStrategy,
    TaskEventType,
    TaskState,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from ..protocols import responses as responses_protocol
from ..protocols.normalized import enforce_context_budget
from ..repositories.models import ApiKey, ImConnection, RequestTask, TaskEvent, User
from ..repositories.tasks import TaskRepository
from ..services.admission import AdmissionService
from ..services.effective_models import EffectiveModelService

# 终态 -> 对外稳定错误码（§16.3 状态映射）。
# completed 不在此表，对应 public_error_code 保持 None。
_TERMINAL_PUBLIC_ERROR_CODE: dict[TaskState, str] = {
    TaskState.FAILED: "upstream_error",
    TaskState.TIMED_OUT: "request_timeout",
    TaskState.CANCELLED: "cancelled",
}


class InferenceService:
    def __init__(self) -> None:
        self.tasks = TaskRepository()
        self.admission = AdmissionService()
        self.models = EffectiveModelService()

    # ------------------------------------------------------------------
    # 任务创建（§12.2）
    # ------------------------------------------------------------------

    def create_task(
        self,
        session: Session,
        *,
        key: ApiKey,
        owner: User,
        protocol: InferenceProtocol,
        parsed: Any,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> RequestTask:
        """创建任务并投递；调用方负责提交事务（失败整体回滚，名额不留存）。"""
        model_row = self.models.resolve(session, key, parsed.model)
        if model_row is None:
            raise DomainError(
                DomainErrorCode.MODEL_NOT_FOUND,
                "The requested model does not exist or is not available.",
                status_code=404,
            )
        self.admission.acquire_slot(session, key, owner)

        previous_task: RequestTask | None = None
        if isinstance(parsed, responses_protocol.ResponsesRequest) and parsed.previous_response_id:
            previous_task = self._resolve_previous(session, parsed.previous_response_id, key)

        normalized = self._build_normalized(session, parsed, previous_task, protocol)

        task = RequestTask(
            public_id=self._task_public_id(),
            response_public_id=(
                f"resp_{secrets.token_hex(16)}"
                if protocol is InferenceProtocol.OPENAI_RESPONSES
                else None
            ),
            previous_task_id=previous_task.id if previous_task else None,
            owner_user_id=owner.id,
            api_key_id=key.id,
            api_key_prefix_snapshot=key.key_prefix,
            fake_model_id=model_row.id,
            requested_model=parsed.model,
            protocol=protocol,
            raw_payload_json=raw_body.decode("utf-8"),
            normalized_request_json=json.dumps(normalized, ensure_ascii=False),
            request_headers_json=json.dumps(headers, ensure_ascii=False) if headers else None,
            stream_requested=parsed.stream,
            reply_strategy_snapshot=key.reply_strategy,
            delivery_mode_snapshot=key.delivery_mode,
            im_connection_id_snapshot=key.im_connection_id,
            llm_config_id_snapshot=key.llm_config_id,
            human_deadline_at=utc_now() + timedelta(seconds=key.human_timeout_seconds),
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=utc_now(),
        )
        self.tasks.add(session, task)
        session.flush()
        self._event(
            session,
            task,
            TaskEventType.CREATED,
            ActorType.SYSTEM,
            {"protocol": protocol.value, "stream": parsed.stream},
        )
        self._deliver(session, task)
        return task

    # ------------------------------------------------------------------
    # 历史响应引用（§12.5）
    # ------------------------------------------------------------------

    def _resolve_previous(
        self, session: Session, previous_response_id: str, key: ApiKey
    ) -> RequestTask:
        task = session.execute(
            select(RequestTask).where(RequestTask.response_public_id == previous_response_id)
        ).scalar_one_or_none()
        if task is None or task.api_key_id != key.id or task.state is not TaskState.COMPLETED:
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                "Invalid previous_response_id.",
                status_code=400,
                public_code="invalid_previous_response_id",
            )
        # 链深校验：沿 previous_task_id 计祖先数，超限整请求 400。
        depth = 0
        cursor = task
        while cursor is not None and cursor.previous_task_id is not None:
            depth += 1
            if depth > MAX_CONTEXT_CHAIN_DEPTH:
                raise DomainError(
                    DomainErrorCode.CONTEXT_LENGTH_EXCEEDED,
                    "Context chain depth exceeds the maximum.",
                    status_code=400,
                )
            cursor = session.get(RequestTask, cursor.previous_task_id)
            # 链断裂（祖先被删除）属非法链（§12.5）：当前 FK 无 ondelete，
            # 实际不会发生，但显式 400 比静默返回缺失祖先的任务更稳。
            if cursor is None:
                raise DomainError(
                    DomainErrorCode.INVALID_REQUEST,
                    "Invalid previous_response_id.",
                    status_code=400,
                    public_code="invalid_previous_response_id",
                )
        return task

    def _build_normalized(
        self,
        session: Session,
        parsed: Any,
        previous_task: RequestTask | None,
        protocol: InferenceProtocol,
    ) -> dict[str, Any]:
        """构造规范化请求 + 等价展开的历史上下文（唯一语义，§12.5）。"""
        if not isinstance(parsed, responses_protocol.ResponsesRequest):
            normalized = parsed.normalized_request()
            enforce_context_budget(normalized["context"])
            return normalized

        context: list[Any] = []
        if previous_task is not None:
            parent_normalized = json.loads(previous_task.normalized_request_json)
            parent_context = parent_normalized.get("context") or []
            # 唯一语义：父任务 context 已含其全部祖先上下文，直接继承。
            context.extend(parent_context)
            if previous_task.response_payload_json:
                draft = ReplyDraft.model_validate_json(previous_task.response_payload_json)
                items, _ = responses_protocol.reply_output_items(draft)
                context.extend(items)
        context.extend(parsed.base_context_items())
        normalized = parsed.normalized_request(context)
        enforce_context_budget(context)
        return normalized

    # ------------------------------------------------------------------
    # 投递与终态
    # ------------------------------------------------------------------

    def _deliver(self, session: Session, task: RequestTask) -> None:
        # llm 策略全程自动转发，无需人工介入：跳过 IM 投递避免无意义
        # 打扰（任务在 Web 仍始终可见）；human / human_fallback_llm 的
        # 人工等待阶段照常投递。
        if task.reply_strategy_snapshot is ReplyStrategy.LLM:
            return
        if task.delivery_mode_snapshot is not DeliveryMode.IM:
            return
        if not task.im_connection_id_snapshot:
            return
        connection = session.get(ImConnection, task.im_connection_id_snapshot)
        if connection is None:
            return
        from .delivery_service import DeliveryService

        # DeliveryService 自身记录投递事件；失败不影响 Web 任务可见性。
        DeliveryService().deliver_task(session, task=task, connection=connection)

    def finalize(
        self,
        session: Session,
        task: RequestTask,
        state: TaskState,
        *,
        allowed_sources: frozenset[TaskState] | set[TaskState] | None = None,
    ) -> bool:
        """推进到终态并幂等释放名额（只有裁决成功方才扣减用户计数）。

        终态失败/超时/取消路径同时写入对外稳定错误码（§16.3），便于
        M6-B 任务工作台直接展示协议层错误，无需重建 DomainError 路径。
        completed 保持 None（成功无错误码）。
        allowed_sources 透传给仓库层做源状态防御（超时末梢防人工先到覆盖）。
        """
        if self.tasks.release_slot_to_terminal(
            session, task.id, state, allowed_sources=allowed_sources
        ):
            self.admission.release_slot(session, task.owner_user_id)
            event_type = {
                TaskState.COMPLETED: TaskEventType.COMPLETED,
                TaskState.FAILED: TaskEventType.FAILED,
                TaskState.TIMED_OUT: TaskEventType.TIMED_OUT,
                TaskState.CANCELLED: TaskEventType.CANCELLED,
            }[state]
            public_code = _TERMINAL_PUBLIC_ERROR_CODE.get(state)
            if public_code is not None:
                # release_slot_to_terminal 走 Core UPDATE 不回填 ORM 对象，
                # 需显式 set，使同一 session 后续视图/事件读取一致。
                task.public_error_code = public_code
            self._event(session, task, event_type, ActorType.SYSTEM)
            return True
        return False

    def cancel_caller_disconnected(self, session: Session, task_id: int) -> bool:
        if self.tasks.cancel_caller_disconnected(session, task_id):
            task = self.tasks.get(session, task_id)
            if task is not None:
                self.admission.release_slot(session, task.owner_user_id)
                self._event(session, task, TaskEventType.CANCELLED, ActorType.CALLER)
            return True
        return False

    def mark_responding(self, session: Session, task: RequestTask) -> None:
        task.state = TaskState.RESPONDING
        task.response_started_at = utc_now()
        self._event(session, task, TaskEventType.STREAM, ActorType.SYSTEM)

    # ------------------------------------------------------------------

    @staticmethod
    def _task_public_id() -> str:
        return f"task_{secrets.token_hex(16)}"

    def _event(
        self,
        session: Session,
        task: RequestTask,
        event_type: TaskEventType,
        actor_type: ActorType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        from ..core.logging import get_request_id

        session.add(
            TaskEvent(
                task_id=task.id,
                event_type=event_type,
                actor_type=actor_type,
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
                request_id=get_request_id(),
            )
        )

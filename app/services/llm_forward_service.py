"""LLM 自动转发服务（M7-C）。

- `llm` 策略：任务创建后直接进入转发（不经 WAITING_HUMAN），结果写回
  RESPONSE_READY，由推理端点既有伪流式路径输出。
- `human_fallback_llm` 策略：人工等待超时后通过 claim_fallback 原子声明
  一次转发权（WAITING_HUMAN -> FORWARDING_LLM），失败即终态 TIMED_OUT，
  不重试。
- 仅同协议转发（Chat/Responses -> openai_compatible；Anthropic -> anthropic）；
  跨协议返回 400 `unsupported_parameter`（完整字段矩阵见 docs/API_CONTRACT.md
  §12.6，跨协议转换在后续阶段逐项开放）。
- 身份 system 指令：从 Fake Model description 派生，追加在调用方已有
  system 内容之后（§12.4 请求保真）。
- 上游响应解析为统一 ReplyDraft；响应 model 由既有渲染器改写为 Fake Model。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    ActorType,
    AuditAction,
    InferenceProtocol,
    LLMProtocol,
    ReplyStrategy,
    TaskEventType,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from ..repositories.llm_configs import LlmConfigRepository
from ..repositories.models import FakeModel, LlmConfig, RequestTask, TaskEvent
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository
from . import llm_upstream
from .llm_draft_service import (
    _build_anthropic_request,
    _build_chat_request,
    _decrypt_config,
    _parse_anthropic_response,
    _parse_chat_response,
)

# Inference protocol -> 允许的 LLM 协议（与 M7-B 生成一致：仅同协议）。
_INFERENCE_TO_LLM: dict[InferenceProtocol, LLMProtocol] = {
    InferenceProtocol.OPENAI_CHAT: LLMProtocol.OPENAI_COMPATIBLE,
    InferenceProtocol.OPENAI_RESPONSES: LLMProtocol.OPENAI_COMPATIBLE,
    InferenceProtocol.ANTHROPIC_MESSAGES: LLMProtocol.ANTHROPIC,
}

# 身份指令兜底（Fake Model 无 description 时）。
_IDENTITY_FALLBACK = (
    "You are a helpful assistant. Respond naturally and concisely in the language the user uses."
)


def identity_system_message(fake_model: FakeModel | None, model_id: str) -> str:
    """从 Fake Model description 派生身份 system 指令（§12.4）。

    description 已声明模型人格/能力时直接使用；否则使用通用兜底文案。
    指令在协议适配器中追加到调用方已有 system 内容之后。
    """
    description = (fake_model.description or "").strip() if fake_model else ""
    if description:
        return f"[{model_id}] {description}"
    return f"[{model_id}] {_IDENTITY_FALLBACK}"


def _inject_identity_chat(body: dict[str, Any], identity: str) -> dict[str, Any]:
    """Chat 请求：身份指令追加在已有 system 内容之后。"""
    messages = list(body.get("messages") or [])
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            merged = {"role": "system", "content": f"{message.get('content') or ''}\n\n{identity}"}
            messages[index] = merged
            return {**body, "messages": messages}
    messages.insert(0, {"role": "system", "content": identity})
    return {**body, "messages": messages}


def _inject_identity_anthropic(body: dict[str, Any], identity: str) -> dict[str, Any]:
    """Anthropic 请求：system 为字符串时追加；块数组时附加文本块。"""
    system = body.get("system")
    if system is None:
        return {**body, "system": identity}
    if isinstance(system, str):
        return {**body, "system": f"{system}\n\n{identity}"}
    if isinstance(system, list):
        return {**body, "system": [*system, {"type": "text", "text": identity}]}
    return body


class LlmForwardService:
    def __init__(self) -> None:
        self.llm_repo = LlmConfigRepository()
        self.tasks = TaskRepository()
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------

    def resolve_config(
        self, session: Session, task: RequestTask
    ) -> tuple[LlmConfig, FakeModel | None]:
        """按任务快照解析 LLM 配置与 Fake Model（协议任意组合）。

        同协议走既有直拼路径；跨协议由 cross 矩阵逐项转换或拒绝
        （docs/API_CONTRACT.md §12.6）。
        """
        config_id = task.llm_config_id_snapshot
        if config_id is None:
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "任务缺少 LLM 配置快照",
                status_code=500,
            )
        cfg = self.llm_repo.get(session, config_id)
        if cfg is None:
            raise DomainError(
                DomainErrorCode.UPSTREAM_ERROR,
                "LLM 配置已删除，无法转发",
                status_code=500,
            )
        fake_model = session.get(FakeModel, task.fake_model_id) if task.fake_model_id else None
        return cfg, fake_model

    # ------------------------------------------------------------------
    # 转发执行
    # ------------------------------------------------------------------

    async def forward(
        self, session: Session, task: RequestTask, *, reason: str
    ) -> tuple[bool, ReplyDraft | None, str | None]:
        """执行一次转发（非流式上游）；返回 (accepted, draft, error_code)。

        accepted=False 表示声明失败（他人已裁决）或转发失败；
        调用方据此推进终态。转发失败不重试（fallback 只转发一次）。
        """
        result = await self._forward_inner(session, task, reason=reason, stream=False)
        return result

    async def forward_stream(
        self, session: Session, task: RequestTask, *, reason: str
    ) -> tuple[bool, list[Any] | None, str | None]:
        """执行一次流式转发；返回 (accepted, chunks, error_code)。

        上游以 SSE 接收，增量归一为 UpstreamChunk 列表；接收完成后聚合为
        ReplyDraft 并按既有原子裁决写回（完整结果先持久化，§13.3 伪流式
        输出前已落库的语义保持）。调用方拿到 chunks 后即可回放输出。
        """
        return await self._forward_inner(session, task, reason=reason, stream=True)

    async def _forward_inner(
        self, session: Session, task: RequestTask, *, reason: str, stream: bool
    ) -> tuple[bool, Any, str | None]:
        """转发内核：声明 -> 上游（流式/非流式）-> 原子接受。"""
        # 原子声明转发权：WAITING_HUMAN -> FORWARDING_LLM（唯一入口）。
        if not self.tasks.claim_fallback(session, task.id):
            return False, None, "claim_lost"
        session.commit()
        self._event(
            session,
            task,
            TaskEventType.FALLBACK,
            ActorType.SYSTEM,
            {"reason": reason},
        )
        session.commit()

        try:
            cfg, fake_model = self.resolve_config(session, task)
            if stream:
                chunks = await self._call_upstream_stream(session, task, cfg, fake_model)
            else:
                draft = await self._call_upstream(session, task, cfg, fake_model)
        except DomainError as exc:
            return False, None, exc.code.value

        if stream:
            # 聚合流式增量为 ReplyDraft（完整结果先持久化再回放，§13.3）。
            collected: dict[str, Any] = {}
            for chunk in chunks:
                llm_upstream.collect_chunk(collected, chunk)
            summary = llm_upstream.finalize_collected(collected)
            draft = ReplyDraft(
                reasoning=summary["reasoning"],
                tool_calls=summary["tool_calls"],
                final_text=summary["final_text"],
            )

        payload = draft.model_dump_json(exclude_none=True)
        # 不依赖 ORM 缓存版本：以 claim 后的 DB 实际版本为准（SQLite RETURNING
        # 或 flush 回填都可能使 ORM 值漂移，读库最稳）。
        current_version = session.execute(
            select(RequestTask.version).where(RequestTask.id == task.id)
        ).scalar_one()
        accepted = self.tasks.accept_forward_reply(
            session,
            task_id=task.id,
            owner_user_id=task.owner_user_id,
            expected_version=current_version,
            response_payload_json=payload,
        )
        if not accepted:
            return False, None, "reply_lost"
        self._event(
            session,
            task,
            TaskEventType.REPLY_SUBMITTED,
            ActorType.UPSTREAM,
            {"source": "llm_forward", "reason": reason},
        )
        self.audit.add(
            session,
            action=AuditAction.LLM_FORWARD_COMPLETED,
            resource_type="request_task",
            resource_id=str(task.id),
            actor_user_id=task.owner_user_id,
            owner_user_id=task.owner_user_id,
            metadata={"reason": reason, "fields": ["response_payload"]},
        )
        session.commit()
        return True, draft, None

    def build_upstream_request(
        self,
        task: RequestTask,
        cfg: LlmConfig,
        fake_model: FakeModel | None,
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        """构造目标协议请求体：同协议直拼，跨协议走 cross 矩阵。"""
        from ..protocols import cross

        expected = _INFERENCE_TO_LLM.get(task.protocol)
        identity = identity_system_message(fake_model, task.requested_model)
        if cfg.protocol is LLMProtocol.OPENAI_COMPATIBLE:
            if expected is LLMProtocol.OPENAI_COMPATIBLE:
                body = _build_chat_request(real_model=cfg.real_model, normalized=normalized)
            else:
                body = cross.to_chat_request(normalized, cfg.real_model)
            return _inject_identity_chat(body, identity)
        if expected is LLMProtocol.ANTHROPIC:
            body = _build_anthropic_request(
                real_model=cfg.real_model,
                normalized=normalized,
                max_tokens=int(normalized.get("max_tokens") or 1024),
            )
        else:
            body = cross.to_anthropic_request(normalized, cfg.real_model)
        return _inject_identity_anthropic(body, identity)

    async def _call_upstream(
        self,
        session: Session,
        task: RequestTask,
        cfg: LlmConfig,
        fake_model: FakeModel | None,
    ) -> ReplyDraft:
        secret, headers = _decrypt_config(cfg)
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, json.JSONDecodeError):
            normalized = {}
        body = self.build_upstream_request(task, cfg, fake_model, normalized)
        if cfg.protocol is LLMProtocol.OPENAI_COMPATIBLE:
            upstream = await llm_upstream.post_chat_completions(
                base_url=cfg.base_url,
                api_key=secret,
                request_body=body,
                extra_headers=headers,
                timeout_seconds=cfg.timeout_seconds,
            )
            return _parse_chat_response(upstream)
        upstream = await llm_upstream.post_anthropic_messages(
            base_url=cfg.base_url,
            api_key=secret,
            request_body=body,
            extra_headers=headers,
            timeout_seconds=cfg.timeout_seconds,
        )
        return _parse_anthropic_response(upstream)

    async def _call_upstream_stream(
        self,
        session: Session,
        task: RequestTask,
        cfg: LlmConfig,
        fake_model: FakeModel | None,
    ) -> list[Any]:
        """流式接收上游增量（UpstreamChunk 列表），由内核统一聚合接受。"""
        from ..services.llm_upstream import UpstreamChunk

        secret, headers = _decrypt_config(cfg)
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, json.JSONDecodeError):
            normalized = {}
        body = self.build_upstream_request(task, cfg, fake_model, normalized)
        chunks: list[UpstreamChunk] = []
        if cfg.protocol is LLMProtocol.OPENAI_COMPATIBLE:
            async for chunk in llm_upstream.stream_chat_completions(
                base_url=cfg.base_url,
                api_key=secret,
                request_body=body,
                extra_headers=headers,
                timeout_seconds=cfg.timeout_seconds,
            ):
                chunks.append(chunk)
        else:
            async for chunk in llm_upstream.stream_anthropic_messages(
                base_url=cfg.base_url,
                api_key=secret,
                request_body=body,
                extra_headers=headers,
                timeout_seconds=cfg.timeout_seconds,
            ):
                chunks.append(chunk)
        return chunks

    # ------------------------------------------------------------------

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


def strategy_uses_llm(strategy: ReplyStrategy) -> bool:
    return strategy in (ReplyStrategy.LLM, ReplyStrategy.HUMAN_FALLBACK_LLM)

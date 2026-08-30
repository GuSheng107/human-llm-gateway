"""Web 小助手用例：会话/消息 CRUD 与 LLM 调用（docs/API_CONTRACT.md §10）。

- 会话与消息严格按 owner 隔离（他人会话 404，防存在性探测）。
- 每条 user 消息携带发送时的页面上下文快照（经 redaction 双层过滤后
  落库）；切换页面不回写历史消息。
- LLM 调用复用用户的 llm_configs（解密凭据仅在服务端内存使用），
  历史消息以 Chat 形态送上游（含上下文快照摘要注入 user 消息）。
- 第一阶段只生成文本与建议，不提供可执行系统工具。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.db import begin_immediate_if_sqlite
from ...core.time import utc_now
from ...domain.enums import AssistantRole, LLMProtocol
from ...domain.errors import DomainError, DomainErrorCode
from ...repositories.llm_configs import LlmConfigRepository
from ...repositories.models import (
    AssistantMessage,
    AssistantSession,
    User,
)
from .. import llm_upstream
from ..llm_config_service import LlmConfigService
from .redaction import build_page_context, log_redaction, redact_text

# 会话消息历史上限（送上游时截断到最近 N 条，防 token 爆炸）。
_MAX_HISTORY_MESSAGES = 40
# 单条消息文本上限。
_MAX_MESSAGE_CHARS = 20000


class AssistantService:
    def __init__(self) -> None:
        self.llm_repo = LlmConfigRepository()

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    def list_sessions(self, session: Session, *, user: User) -> list[AssistantSession]:
        return list(
            session.scalars(
                select(AssistantSession)
                .where(
                    AssistantSession.owner_user_id == user.id,
                    AssistantSession.deleted_at.is_(None),
                )
                .order_by(
                    AssistantSession.last_message_at.desc().nullslast(),
                    AssistantSession.id.desc(),
                )
            )
        )

    def create_session(
        self,
        session: Session,
        *,
        user: User,
        title: str,
        llm_config_id: int | None,
    ) -> AssistantSession:
        begin_immediate_if_sqlite(session)
        cleaned_title = (title or "").strip() or "新会话"
        if len(cleaned_title) > 255:
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "会话标题过长", status_code=400)
        config_id = self._validate_llm_config(session, user, llm_config_id)
        row = AssistantSession(
            owner_user_id=user.id,
            title=cleaned_title,
            llm_config_id=config_id,
        )
        session.add(row)
        session.flush()
        return row

    def get_session(self, session: Session, *, user: User, session_id: int) -> AssistantSession:
        row = session.get(AssistantSession, session_id)
        if row is None or row.deleted_at is not None or row.owner_user_id != user.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "会话不存在", status_code=404)
        return row

    def delete_session(self, session: Session, *, user: User, session_id: int) -> None:
        row = self.get_session(session, user=user, session_id=session_id)
        begin_immediate_if_sqlite(session)
        row.deleted_at = utc_now()
        session.flush()

    def list_messages(
        self, session: Session, *, session_row: AssistantSession
    ) -> list[AssistantMessage]:
        return list(
            session.scalars(
                select(AssistantMessage)
                .where(AssistantMessage.session_id == session_row.id)
                .order_by(AssistantMessage.id.asc())
            )
        )

    # ------------------------------------------------------------------
    # 消息发送（含 LLM 调用）
    # ------------------------------------------------------------------

    async def send_message(
        self,
        session: Session,
        *,
        user: User,
        session_row: AssistantSession,
        text: str,
        page_context_raw: dict[str, Any] | None,
    ) -> AssistantMessage:
        """发送 user 消息并同步取回 LLM 回复；两步在同一事务落库。"""
        begin_immediate_if_sqlite(session)
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "消息不能为空", status_code=400)
        if len(cleaned_text) > _MAX_MESSAGE_CHARS:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"消息过长（上限 {_MAX_MESSAGE_CHARS} 字符）",
                status_code=400,
            )

        # 上下文校验 + 脱敏（拒绝或擦洗，落库的是干净快照）。
        context_json: str | None = None
        route: str | None = None
        feature: str | None = None
        version: int | None = None
        redaction_hits = 0
        if page_context_raw is not None:
            try:
                context, redaction_hits = build_page_context(
                    route=str(page_context_raw.get("route", ""))[:255],
                    feature=str(page_context_raw.get("feature", "")),
                    resource=page_context_raw.get("resource") or {},
                    unsaved_edit=page_context_raw.get("unsaved_edit"),
                    context_version=int(page_context_raw.get("context_version", 1)),
                )
            except (TypeError, ValueError) as exc:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "页面上下文结构非法",
                    status_code=400,
                ) from exc
            context_json = json.dumps(context, ensure_ascii=False)
            route = context["route"]
            feature = context["feature"]
            version = context["context_version"]

        # 用户消息文本同样擦洗（可能粘贴了凭据）：落库与送上游的都是干净版。
        clean_text, text_hits = redact_text(cleaned_text)
        redaction_hits += text_hits

        user_message = AssistantMessage(
            session_id=session_row.id,
            role=AssistantRole.USER,
            content_json=json.dumps({"text": clean_text}, ensure_ascii=False),
            page_context_json=context_json,
            page_route=route,
            page_feature=feature,
            context_version=version,
        )
        session.add(user_message)
        session.flush()
        log_redaction(f"session:{session_row.id}:msg:{user_message.id}", redaction_hits)

        # LLM 回复：同步调用（第一阶段无流式）；失败时 user 消息仍保留，
        # 错误透传给前端（不吞消息）。历史（含刚写入的干净 user 消息）
        # 由 _call_llm 自行加载。
        config_id = session_row.llm_config_id
        reply_text, metadata = await self._call_llm(
            session,
            user=user,
            config_id=config_id,
            context_json=context_json,
            session_id=session_row.id,
        )
        reply_message = AssistantMessage(
            session_id=session_row.id,
            role=AssistantRole.ASSISTANT,
            content_json=json.dumps({"text": reply_text}, ensure_ascii=False),
            upstream_metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(reply_message)
        session_row.last_message_at = utc_now()
        session.flush()
        return reply_message

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _validate_llm_config(
        self, session: Session, user: User, llm_config_id: int | None
    ) -> int | None:
        if llm_config_id is None:
            return None
        cfg = self.llm_repo.get(session, llm_config_id)
        if cfg is None or cfg.owner_user_id != user.id:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "LLM 配置必须是自己的有效配置",
                status_code=400,
            )
        if not cfg.is_enabled:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "LLM 配置已停用",
                status_code=400,
            )
        return llm_config_id

    async def _call_llm(
        self,
        session: Session,
        *,
        user: User,
        config_id: int | None,
        context_json: str | None,
        session_id: int,
    ) -> tuple[str, dict[str, Any]]:
        """调用用户 LLM 配置生成回复；返回 (回复文本, 脱敏元数据)。"""
        if config_id is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "会话未绑定 LLM 配置",
                status_code=400,
            )
        cfg = self.llm_repo.get(session, config_id)
        if cfg is None or cfg.owner_user_id != user.id or not cfg.is_enabled:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "LLM 配置不可用",
                status_code=400,
            )
        secret, headers = LlmConfigService.get_secret_pair(session, cfg)

        # 组装 Chat messages：系统定位 + 历史轮次 + 本条（含上下文摘要）。
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are the built-in assistant of a human-LLM gateway admin "
                    "console. Answer in the user's language. You only produce "
                    "text and suggestions; you cannot execute actions."
                ),
            }
        ]
        history = (
            session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.session_id == session_id)
                .order_by(AssistantMessage.id.desc())
                .limit(_MAX_HISTORY_MESSAGES)
            )
            .scalars()
            .all()
        )
        for message in reversed(history):
            if message.id is None:
                continue
            role = "assistant" if message.role is AssistantRole.ASSISTANT else "user"
            try:
                content = json.loads(message.content_json).get("text", "")
            except (ValueError, TypeError):
                content = ""
            if content:
                messages.append({"role": role, "content": content})
        # 本条 user 消息在 history 末尾（刚 flush）；附上下文快照。
        if context_json and messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = (
                f"{messages[-1]['content']}\n\n[Page context snapshot]\n{context_json}"
            )

        body: dict[str, Any] = {
            "model": cfg.real_model,
            "messages": messages,
        }
        if cfg.protocol is LLMProtocol.OPENAI_COMPATIBLE:
            upstream = await llm_upstream.post_chat_completions(
                base_url=cfg.base_url,
                api_key=secret,
                request_body=body,
                extra_headers=headers,
                timeout_seconds=cfg.timeout_seconds,
            )
        else:
            upstream = await llm_upstream.post_anthropic_messages(
                base_url=cfg.base_url,
                api_key=secret,
                request_body={
                    "model": cfg.real_model,
                    "max_tokens": 2048,
                    "messages": [m for m in messages if m["role"] != "system"],
                    **({"system": messages[0]["content"]} if messages else {}),
                },
                extra_headers=headers,
                timeout_seconds=cfg.timeout_seconds,
            )
        reply_text, finish = self._extract_reply(upstream)
        usage = upstream.get("usage") or {}
        metadata = {
            "finish_reason": finish,
            "usage": {
                k: usage.get(k) for k in ("prompt_tokens", "completion_tokens") if k in usage
            },
        }
        return reply_text, metadata

    @staticmethod
    def _extract_reply(upstream: dict[str, Any]) -> tuple[str, str]:
        """从上游响应提取回复文本与结束原因（双协议形态）。"""
        choices = upstream.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            return (
                content if isinstance(content, str) else str(content),
                choices[0].get("finish_reason") or "stop",
            )
        content = upstream.get("content")
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(parts), upstream.get("stop_reason") or "stop"
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游响应缺少回复内容", status_code=502)


def count_active_sessions(session: Session, user_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(AssistantSession)
            .where(
                AssistantSession.owner_user_id == user_id,
                AssistantSession.deleted_at.is_(None),
            )
        )
        or 0
    )

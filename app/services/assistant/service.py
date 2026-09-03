"""Web 小助手用例：会话/消息 CRUD 与 LLM 调用（docs/API_CONTRACT.md §10）。

- 会话与消息严格按 owner 隔离（他人会话 404，防存在性探测）。
- 每条 user 消息携带发送时的页面上下文快照（经 redaction 双层过滤后
  落库）；切换页面不回写历史消息。
- LLM 调用复用用户的 llm_configs（解密凭据仅在服务端内存使用），
  历史消息以 Chat 形态送上游（含上下文快照摘要注入 user 消息）。
- 支持同步与 SSE 流式两种回复取回方式（共用同一条落库路径）。
- 支持 MCP 工具调用：上游返回 tool_calls 时自动执行只读工具并将
  结果追加到对话后再次调用 LLM 获取最终回复。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.db import begin_immediate_if_sqlite
from ...core.time import iso_utc, utc_now
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
from ..llm_draft_service import _apply_config
from .redaction import build_page_context, log_redaction, redact_text

# 会话消息历史上限（送上游时截断到最近 N 条，防 token 爆炸）。
_MAX_HISTORY_MESSAGES = 40
# 单条消息文本上限。
_MAX_MESSAGE_CHARS = 20000
# tool_call 自动执行最大轮次（防止无限递归）。
_MAX_TOOL_ROUNDS = 5

# 页面 feature -> 功能描述（注入系统提示词，帮助 LLM 理解用户当前所在页面）。
_FEATURE_DESCRIPTIONS: dict[str, str] = {
    "console": "控制台首页：展示待回复任务队列、今日统计概览和快捷入口。",
    "task_list": "任务列表：按状态筛选和搜索待回复/已回复/超时等任务。",
    "task_detail": "任务详情/回复：查看单条任务的完整对话历史并输入回复内容。",
    "api_keys": "API Key 管理：创建、查看、停用供外部调用方使用的 API Key。",
    "llm_configs": "LLM 配置：管理上游 LLM 服务的连接信息（地址、密钥、模型、协议）。",
    "connections": "IM 连接管理：配置和监控与飞书/企微/钉钉等 IM 平台的连接状态。",
    "models": "Fake Model 目录：管理对外暴露的虚拟模型列表和分组映射。",
    "invitations": "邀请码管理：生成和管理用户注册邀请码。",
    "users": "用户管理：查看和管理系统用户账号与角色。",
    "account": "个人设置：修改密码、查看个人信息。",
}

# 系统提示词模板（{feature_description} 占位符按当前页面动态填充）。
_SYSTEM_PROMPT_TEMPLATE = """\
你是 Human LLM Gateway（能工智人网关）管理台的内置 AI 助手。请用用户的语言回答。

## 系统简介
本系统是一个人工伪装 LLM 的 API 网关。外部调用方通过 OpenAI/Anthropic SDK 向本系统\
发起推理请求，回复内容由人类操作员手动输入或由真实 LLM 自动生成。系统负责任务\
分发、IM 投递、协议转换和回复伪装，让调用方无感知地获得人工或自动回复。

## 你的能力
- 解释当前页面的功能和使用方法
- 基于用户提供的任务草稿给出改进建议
- 回答关于系统配置、连接、API Key 等问题
- 提供排查建议和操作指引

## 能力边界
你只能生成文本和建议，不能直接执行任何系统操作（不能创建/修改/删除资源，\
不能提交回复，不能调用 API）。如果用户的请求需要操作，请给出具体步骤指引。

## 当前页面上下文
用户每条消息可能附带 [Page context snapshot]，其中包含：
- route: 当前页面路径
- feature: 页面功能标识
- resource: 当前选中或操作的资源信息（如任务 ID、筛选条件）
- unsaved_edit: 用户正在编辑但尚未提交的草稿内容

{feature_hint}请基于这些信息给出针对性的回答。\
"""


def _build_system_prompt(context_json: str | None) -> str:
    """根据当前页面上下文构建系统提示词。"""
    feature_description = ""
    if context_json:
        try:
            ctx = json.loads(context_json)
            feature = ctx.get("feature", "")
            desc = _FEATURE_DESCRIPTIONS.get(feature)
            if desc:
                feature_description = f"当前页面功能：{desc}\n\n"
        except (ValueError, TypeError):
            pass
    return _SYSTEM_PROMPT_TEMPLATE.format(
        feature_hint=feature_description,
    )


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
                .where(AssistantSession.owner_user_id == user.id)
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
        if row is None or row.owner_user_id != user.id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "会话不存在", status_code=404)
        return row

    def delete_session(self, session: Session, *, user: User, session_id: int) -> None:
        row = self.get_session(session, user=user, session_id=session_id)
        begin_immediate_if_sqlite(session)
        session.delete(row)
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
    # 消息发送（含 LLM 调用）：同步与流式共用同一条落库路径
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
        context_json = self._save_user_message(
            session,
            user=user,
            session_row=session_row,
            text=text,
            page_context_raw=page_context_raw,
        )
        reply_text, metadata = await self._call_llm(
            session,
            user=user,
            config_id=session_row.llm_config_id,
            context_json=context_json,
            session_id=session_row.id,
        )
        return self._append_reply_message(
            session,
            owner_user_id=user.id,
            assistant_session_id=session_row.id,
            reply_text=reply_text,
            metadata=metadata,
        )

    async def stream_message(
        self,
        session: Session,
        *,
        user: User,
        session_row: AssistantSession,
        text: str,
        page_context_raw: dict[str, Any] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式发送：user 消息落库后逐段转发上游增量，结束后落库回复。

        支持 tool_call 自动执行：若上游返回 tool_calls，静默执行工具后
        重新发起流式请求，直到获得纯文本回复。tool_call 轮次不向客户端
        发送 delta，仅最终文本回复才 yield。

        事件协议（API 层负责 SSE 编码）：
        - ``{"type": "delta", "text": ...}``：回复增量。
        - ``{"type": "done", "message": {...}}``：回复已落库，附完整消息。
        异常经 DomainError 抛出（API 层转 error 事件）。
        """
        context_json = self._save_user_message(
            session,
            user=user,
            session_row=session_row,
            text=text,
            page_context_raw=page_context_raw,
        )
        assistant_session_id = session_row.id
        owner_user_id = user.id
        (
            protocol,
            base_url,
            secret,
            timeout_seconds,
            request_body,
        ) = await self._build_request(
            session,
            user=user,
            config_id=session_row.llm_config_id,
            context_json=context_json,
            session_id=assistant_session_id,
        )

        reply_text = ""
        for _round in range(_MAX_TOOL_ROUNDS):
            if protocol is LLMProtocol.OPENAI_CHAT:
                chunk_iter: AsyncIterator[llm_upstream.UpstreamChunk] = (
                    llm_upstream.stream_chat_completions(
                        base_url=base_url,
                        api_key=secret,
                        request_body=request_body,
                        timeout_seconds=timeout_seconds,
                    )
                )
            elif protocol is LLMProtocol.OPENAI_RESPONSES:
                chunk_iter = llm_upstream.stream_responses(
                    base_url=base_url,
                    api_key=secret,
                    request_body=request_body,
                    timeout_seconds=timeout_seconds,
                )
            else:
                chunk_iter = llm_upstream.stream_anthropic_messages(
                    base_url=base_url,
                    api_key=secret,
                    request_body=request_body,
                    timeout_seconds=timeout_seconds,
                )
            collected: dict[str, Any] = {}
            async for chunk in chunk_iter:
                llm_upstream.collect_chunk(collected, chunk)
                if chunk.text:
                    yield {"type": "delta", "text": chunk.text}

            summary = llm_upstream.finalize_collected(collected)
            tool_calls = summary.get("tool_calls")

            if not tool_calls:
                # 无 tool_calls，这是最终回复
                reply_text = summary.get("final_text") or ""
                if not reply_text and not summary.get("reasoning"):
                    raise DomainError(
                        DomainErrorCode.UPSTREAM_ERROR,
                        "上游响应缺少回复内容",
                        status_code=502,
                    )
                break

            # 有 tool_calls：静默执行工具，追加到 messages 后重新流式请求
            messages = request_body.get("messages", [])
            # 构造 assistant tool_calls 消息
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": summary.get("final_text") or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": tc.get("arguments", "{}")
                            if isinstance(tc.get("arguments"), str)
                            else json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)
            for tc in tool_calls:
                tc_name = tc.get("name", "")
                tc_args = tc.get("arguments", {})
                if isinstance(tc_args, str):
                    try:
                        tc_args = json.loads(tc_args)
                    except (ValueError, TypeError):
                        tc_args = {}
                result = self._execute_mcp_tool(session, user, tc_name, tc_args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    }
                )
            request_body["messages"] = messages
            # 不向客户端发送 tool_call 轮次的 delta，继续下一轮

        metadata = {"finish_reason": "stop", "usage": {}}
        reply_message = self._append_reply_message(
            session,
            owner_user_id=owner_user_id,
            assistant_session_id=assistant_session_id,
            reply_text=reply_text,
            metadata=metadata,
        )
        # 流式端点没有 API 层的收尾 commit，这里自行提交。
        session.commit()
        yield {
            "type": "done",
            "message": {
                "id": str(reply_message.id),
                "role": "assistant",
                "text": reply_text,
                "page_context": None,
                "upstream_metadata": metadata,
                "created_at": iso_utc(reply_message.created_at) or "",
            },
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _save_user_message(
        self,
        session: Session,
        *,
        user: User,
        session_row: AssistantSession,
        text: str,
        page_context_raw: dict[str, Any] | None,
    ) -> str | None:
        """校验 + 脱敏 + 落库 user 消息（独立事务提交）；返回 context_json。"""
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
        # 用户消息先独立持久化，随后释放 SQLite 写锁再调用慢上游；上游失败时
        # 也保留这条消息，便于用户重试，而不会锁住全站写请求。
        session.commit()
        return context_json

    def _append_reply_message(
        self,
        session: Session,
        *,
        owner_user_id: int,
        assistant_session_id: int,
        reply_text: str,
        metadata: dict[str, Any],
    ) -> AssistantMessage:
        """落库 assistant 回复并推进会话 last_message_at（调用方负责提交）。"""
        begin_immediate_if_sqlite(session)
        current_session = session.get(AssistantSession, assistant_session_id, with_for_update=True)
        if current_session is None or current_session.owner_user_id != owner_user_id:
            raise DomainError(DomainErrorCode.NOT_FOUND, "会话不存在", status_code=404)
        reply_message = AssistantMessage(
            session_id=assistant_session_id,
            role=AssistantRole.ASSISTANT,
            content_json=json.dumps({"text": reply_text}, ensure_ascii=False),
            upstream_metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(reply_message)
        current_session.last_message_at = utc_now()
        session.flush()
        return reply_message

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

    async def _build_request(
        self,
        session: Session,
        *,
        user: User,
        config_id: int | None,
        context_json: str | None,
        session_id: int,
    ) -> tuple[LLMProtocol, str, str, float, dict[str, Any]]:
        """加载配置与历史并组装上游请求（同步/流式共用）。

        返回 (protocol, base_url, api_key, timeout, request_body)；
        网络 I/O 前结束读取事务（rollback），历史与凭据均已复制到局部。
        """
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
        secret = LlmConfigService.get_secret(session, cfg)

        # 组装 Chat messages：系统定位 + 历史轮次 + 本条（含上下文摘要）。
        system_prompt = _build_system_prompt(context_json)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt,
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

        protocol = cfg.protocol
        base_url = cfg.base_url
        timeout_seconds = cfg.timeout_seconds
        real_model = cfg.real_model
        if protocol is LLMProtocol.OPENAI_CHAT:
            from ..mcp.tools import list_openai_tools

            mcp_tools = list_openai_tools()
            request_body: dict[str, Any] = {
                "model": real_model,
                "messages": messages,
            }
            if mcp_tools:
                request_body["tools"] = mcp_tools
        elif protocol is LLMProtocol.OPENAI_RESPONSES:
            request_body = {
                "model": real_model,
                "instructions": messages[0]["content"],
                "input": [
                    {
                        "role": message["role"],
                        "content": [{"type": "input_text", "text": message["content"]}],
                    }
                    for message in messages[1:]
                ],
            }
        else:
            request_body = {
                "model": real_model,
                "max_tokens": 2048,
                "messages": [m for m in messages if m["role"] != "system"],
                **({"system": messages[0]["content"]} if messages else {}),
            }
        _apply_config(request_body, cfg)
        # 历史与配置均已复制到局部变量；网络 I/O 前结束读取事务。
        session.rollback()
        return protocol, base_url, secret, float(timeout_seconds), request_body

    async def _call_llm(
        self,
        session: Session,
        *,
        user: User,
        config_id: int | None,
        context_json: str | None,
        session_id: int,
    ) -> tuple[str, dict[str, Any]]:
        """调用用户 LLM 配置生成回复；返回 (回复文本, 脱敏元数据)。

        支持 tool_call 自动执行循环：上游返回 tool_calls 时，执行只读
        工具并将结果追加到 messages 后再次调用 LLM，直到获得纯文本回复。
        最多循环 _MAX_TOOL_ROUNDS 轮防止无限递归。
        """
        (
            protocol,
            base_url,
            secret,
            timeout_seconds,
            request_body,
        ) = await self._build_request(
            session,
            user=user,
            config_id=config_id,
            context_json=context_json,
            session_id=session_id,
        )

        total_usage: dict[str, int] = {}
        # 注意：_build_request 末尾已 rollback session 以释放读锁。
        # SQLAlchemy rollback 后 session 仍可执行新查询（只是之前加载的 ORM
        # 对象失效）。tool handler 均通过 Repository 发起全新 select，不依赖
        # 已失效对象，因此安全。
        for _round in range(_MAX_TOOL_ROUNDS):
            upstream = await self._post_upstream(
                protocol, base_url, secret, request_body, timeout_seconds
            )
            # 检查是否有 tool_calls
            tool_calls = self._extract_tool_calls(upstream)
            if not tool_calls:
                # 无 tool_calls，提取最终回复
                reply_text, finish = self._extract_reply(upstream)
                usage = upstream.get("usage") or {}
                for k in ("prompt_tokens", "completion_tokens"):
                    if k in usage:
                        total_usage[k] = total_usage.get(k, 0) + usage[k]
                metadata = {"finish_reason": finish, "usage": total_usage}
                return reply_text, metadata

            # 有 tool_calls：执行工具并追加结果到 messages
            messages = request_body.get("messages", [])
            # 追加 assistant 的 tool_calls 消息
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": upstream.get("choices", [{}])[0].get("message", {}).get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                            if isinstance(tc["arguments"], str)
                            else json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            # 执行每个 tool_call 并追加 tool 结果消息
            for tc in tool_calls:
                result = self._execute_mcp_tool(session, user, tc["name"], tc["arguments"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )

            request_body["messages"] = messages
            # 累加 usage
            usage = upstream.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens"):
                if k in usage:
                    total_usage[k] = total_usage.get(k, 0) + usage[k]

        # 超过最大轮次，强制提取当前回复
        reply_text, finish = self._extract_reply(upstream)
        metadata = {"finish_reason": finish, "usage": total_usage}
        return reply_text, metadata

    async def _post_upstream(
        self,
        protocol: LLMProtocol,
        base_url: str,
        secret: str,
        request_body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """根据协议调用上游 LLM。"""
        if protocol is LLMProtocol.OPENAI_CHAT:
            return await llm_upstream.post_chat_completions(
                base_url=base_url,
                api_key=secret,
                request_body=request_body,
                timeout_seconds=timeout_seconds,
            )
        elif protocol is LLMProtocol.OPENAI_RESPONSES:
            return await llm_upstream.post_responses(
                base_url=base_url,
                api_key=secret,
                request_body=request_body,
                timeout_seconds=timeout_seconds,
            )
        else:
            return await llm_upstream.post_anthropic_messages(
                base_url=base_url,
                api_key=secret,
                request_body=request_body,
                timeout_seconds=timeout_seconds,
            )

    @staticmethod
    def _extract_tool_calls(upstream: dict[str, Any]) -> list[dict[str, Any]]:
        """从上游响应提取 tool_calls（OpenAI Chat 格式）。"""
        choices = upstream.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        message = choices[0].get("message") or {}
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            return []
        result = []
        for tc in raw_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            arguments_raw = fn.get("arguments", "{}")
            try:
                arguments = (
                    json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
                )
            except (ValueError, TypeError):
                arguments = {}
            result.append(
                {
                    "id": tc.get("id", ""),
                    "name": name,
                    "arguments": arguments,
                }
            )
        return result

    @staticmethod
    def _execute_mcp_tool(
        session: Session, user: User, name: str, arguments: dict[str, Any]
    ) -> str:
        """执行 MCP 工具并返回文本结果。"""
        import logging

        from ..mcp.tools import get_mcp_tool

        tool_def = get_mcp_tool(name)
        if tool_def is None:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        try:
            result = tool_def.handler(session, user, arguments)
            # MCP 结果格式：{"content": [{"type": "text", "text": "..."}]}
            contents = result.get("content", [])
            texts = [
                c.get("text", "")
                for c in contents
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "MCP tool %s execution failed: %s", name, exc, exc_info=True
            )
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

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
        output = upstream.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for block in item.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        parts.append(str(block.get("text") or ""))
            if parts:
                return "".join(parts), upstream.get("status") or "completed"
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
            .where(AssistantSession.owner_user_id == user_id)
        )
        or 0
    )

"""LLM 总结（提示条摘要）生成服务。

IM 两消息投递的提示条默认用 prompt 尾部摘要；连接开启 llm_summary_enabled
并绑定 LLM 配置后，投递前用该配置生成 ≤200 字总结替换摘要，让用户不用
读长 prompt 尾部就知道任务要做什么。

设计约束（docs/PRODUCT.md §6.4）：
- 任何失败（配置缺失/解密失败/上游错误/超时/解析为空）一律返回 None，
  投递流程静默降级为尾部摘要，绝不影响任务创建与投递；
- 短超时（LLM_SUMMARY_TIMEOUT_SECONDS）、小输入（尾部 6000 字）、小输出；
- 复用 llm_draft_service 的密钥解密与 llm_upstream 的出站调用（异步）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..core.constants import (
    IM_LLM_COMPRESS_CHARS,
    LLM_SUMMARY_MAX_CHARS,
    LLM_SUMMARY_PROMPT_CHARS,
    LLM_SUMMARY_TIMEOUT_SECONDS,
)
from ..domain.enums import LLMProtocol
from ..repositories.models import ImConnection, LlmConfig, RequestTask
from . import llm_upstream

logger = logging.getLogger(__name__)

_SUMMARY_INSTRUCTION = (
    "你是 IM 投递助手。把下面的任务提问浓缩成一条不超过 200 字的中文摘要，"
    "让收到消息的人一眼看出要做什么。只输出摘要本身，不要任何前缀、引号或解释。\n\n"
    "任务提问（可能被截断，只摘要给定内容）：\n"
)

_COMPRESS_INSTRUCTION = (
    "你是 IM 内容压缩助手。把下面的聊天记录压缩成不超过 500 字的中文摘要，"
    "保留关键信息（提问、约束、关键上下文），去掉冗余。只输出压缩结果本身，"
    "不要任何前缀、引号或解释。\n\n"
    "聊天记录：\n"
)


async def compress_chat(
    session: Session, *, connection: ImConnection, task: RequestTask, chat: str
) -> str | None:
    """按连接的 LLM 配置把聊天记录压缩到 500 字；任何失败返回 None（静默降级）。

    与 summarize_task_prompt 同门禁：连接需开启 llm_summary_enabled 且绑定有效配置。
    chat 为空或超长输入时也返回 None。
    """
    llm_config_id = connection.llm_config_id
    if not connection.llm_summary_enabled or llm_config_id is None:
        return None
    if not chat:
        return None
    try:
        cfg = session.get(LlmConfig, llm_config_id)
        if cfg is None or not cfg.is_enabled:
            return None
        from .llm_draft_service import _decrypt_config

        api_key = _decrypt_config(cfg)
        return await _generate_compress(cfg=cfg, api_key=api_key, chat=chat)
    except Exception:  # 压缩是增强功能，失败不影响外发
        logger.exception("llm chat compress failed (connection %s)", connection.id)
        return None


async def _generate_compress(*, cfg: LlmConfig, api_key: str, chat: str) -> str | None:
    instruction = _COMPRESS_INSTRUCTION + chat
    timeout = float(LLM_SUMMARY_TIMEOUT_SECONDS)
    if cfg.protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        body: dict[str, Any] = {
            "model": cfg.real_model,
            "max_tokens": IM_LLM_COMPRESS_CHARS,
            "messages": [{"role": "user", "content": instruction}],
        }
        payload = await llm_upstream.post_anthropic_messages(
            base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
        )
        return _extract_anthropic(payload)
    if cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
        body = {
            "model": cfg.real_model,
            "max_output_tokens": IM_LLM_COMPRESS_CHARS,
            "input": instruction,
        }
        payload = await llm_upstream.post_responses(
            base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
        )
        return _extract_responses(payload)
    # 默认 OpenAI Chat Completions
    body = {
        "model": cfg.real_model,
        "max_completion_tokens": IM_LLM_COMPRESS_CHARS,
        "messages": [{"role": "user", "content": instruction}],
    }
    payload = await llm_upstream.post_chat_completions(
        base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
    )
    return _extract_chat(payload)


async def summarize_task_prompt(
    session: Session, *, connection: ImConnection, task: RequestTask
) -> str | None:
    """按连接的 LLM 配置生成任务摘要；任何失败返回 None（静默降级）。"""
    llm_config_id = connection.llm_config_id
    if not connection.llm_summary_enabled or llm_config_id is None:
        return None
    try:
        cfg = session.get(LlmConfig, llm_config_id)
        if cfg is None or not cfg.is_enabled:
            return None
        from .llm_draft_service import _decrypt_config

        api_key = _decrypt_config(cfg)
        return await _generate(cfg=cfg, api_key=api_key, task=task)
    except Exception:  # 摘要是增强功能，失败不影响投递
        logger.exception("llm summary generation failed (connection %s)", connection.id)
        return None


async def _generate(*, cfg: LlmConfig, api_key: str, task: RequestTask) -> str | None:
    from .delivery_service import _tail_prompt

    prompt = _tail_prompt(task, cap=LLM_SUMMARY_PROMPT_CHARS)
    if not prompt:
        return None
    instruction = _SUMMARY_INSTRUCTION + prompt
    timeout = float(LLM_SUMMARY_TIMEOUT_SECONDS)
    if cfg.protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        body: dict[str, Any] = {
            "model": cfg.real_model,
            "max_tokens": LLM_SUMMARY_MAX_CHARS,
            "messages": [{"role": "user", "content": instruction}],
        }
        payload = await llm_upstream.post_anthropic_messages(
            base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
        )
        return _extract_anthropic(payload)
    if cfg.protocol is LLMProtocol.OPENAI_RESPONSES:
        body = {
            "model": cfg.real_model,
            "max_output_tokens": LLM_SUMMARY_MAX_CHARS,
            "input": instruction,
        }
        payload = await llm_upstream.post_responses(
            base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
        )
        return _extract_responses(payload)
    # 默认 OpenAI Chat Completions（与 llm_draft_service 一致用 max_completion_tokens）
    body = {
        "model": cfg.real_model,
        "max_completion_tokens": LLM_SUMMARY_MAX_CHARS,
        "messages": [{"role": "user", "content": instruction}],
    }
    payload = await llm_upstream.post_chat_completions(
        base_url=cfg.base_url, api_key=api_key, request_body=body, timeout_seconds=timeout
    )
    return _extract_chat(payload)


def _extract_chat(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    return _clean(content)


def _extract_anthropic(payload: dict[str, Any]) -> str | None:
    parts = [
        block.get("text", "")
        for block in payload.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return _clean("\n".join(part for part in parts if part))


def _extract_responses(payload: dict[str, Any]) -> str | None:
    parts = [
        part.get("text", "")
        for item in payload.get("output") or []
        if isinstance(item, dict) and item.get("type") == "message"
        for part in (item.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "output_text"
    ]
    return _clean("\n".join(part for part in parts if part))


def _clean(text: Any) -> str | None:
    """去掉代码围栏/引号包裹，压成单行并限制长度；空结果返回 None。"""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").lstrip()
    cleaned = cleaned.strip().strip('"').strip("'").strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    return cleaned[:LLM_SUMMARY_MAX_CHARS]

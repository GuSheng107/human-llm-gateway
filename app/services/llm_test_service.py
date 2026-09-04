"""LLM 配置连通性测试（M7-A，docs/API_CONTRACT.md §6）。

按 LLM 协议分流调用真实 endpoint（仅连通性测试，不保存任何响应正文）。
三种协议都发送一次真实生成请求（让模型回复 "hi"），必须得到非空回复才算成功，
仅返回 2xx 但响应为空视为失败（reason_code=empty_reply）：

- `openai_chat`：POST {base_url}/chat/completions（model + messages=[user "hi"]）。
- `openai_responses`：POST {base_url}/responses（model + input="hi"）。
- `anthropic`：POST {base_url}/v1/messages，使用 `Authorization: x-api-key` 与
  `anthropic-version: 2023-06-01`，最小请求体（max_tokens=16, messages=[user "hi"]）。

成功：HTTP 2xx。
失败：超时、网络错误、非 2xx、响应体解析失败 -> 返回 `failed` 与简化的 reason_code，
并把 last_test_result 写入数据库。失败 reason 不回显 Secret。

每次调用 `run_connectivity_test` 都会写入一条 `llm_test.passed` 或
`llm_test.failed` 结构化日志，便于在「日志查询」页中按 traceId 顺藤摸瓜。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..core.constants import LLM_CONNECT_TEST_TIMEOUT_SECONDS
from ..core.logging import log_event
from ..domain.enums import LLMProtocol

logger = logging.getLogger("app.services.llm_test")


@dataclass(frozen=True)
class ConnTestOutcome:
    success: bool
    reason_code: str
    detail: str
    http_status: int | None


def _normalize_anthropic_url(base_url: str) -> str:
    """Anthropic 连通性测试端点：复用 llm_upstream 的兼容归一。"""
    from .llm_upstream import _anthropic_messages_url

    return _anthropic_messages_url(base_url)


def _normalize_openai_chat_url(base_url: str) -> str:
    from .llm_upstream import _chat_completions_url

    return _chat_completions_url(base_url)


def _chat_reply_text(payload: object) -> str:
    """从 OpenAI Chat Completions 响应提取首条消息文本（兼容字符串与分块 content）。"""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


async def _ssrf_precheck(base_url: str) -> ConnTestOutcome | None:
    """连通性测试前的 SSRF 校验；违规返回失败 outcome（不抛错）。"""
    from starlette.concurrency import run_in_threadpool

    from ..core.ssrf import SsrfViolation, validate_base_url

    try:
        await run_in_threadpool(validate_base_url, base_url)
    except SsrfViolation as exc:
        return ConnTestOutcome(False, "blocked", str(exc), None)
    return None


def _anthropic_reply_text(payload: object) -> str:
    """从 Anthropic Messages 响应提取文本块。"""
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts)


def _responses_reply_text(payload: object) -> str:
    """从 OpenAI Responses 响应提取文本（output 数组中的 message.output_text 分片）。"""
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for piece in item.get("content") or []:
            if isinstance(piece, dict) and piece.get("type") in ("output_text", "text"):
                parts.append(str(piece.get("text", "")))
    return "".join(parts)


async def test_openai_chat(
    *,
    base_url: str,
    api_key: str,
    real_model: str,
    timeout_seconds: float = LLM_CONNECT_TEST_TIMEOUT_SECONDS,
) -> ConnTestOutcome:
    """真实生成调用：要求模型回复 "hi"，返回空内容视为不可用的无效配置。"""
    blocked = await _ssrf_precheck(base_url)
    if blocked is not None:
        return blocked
    url = _normalize_openai_chat_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": real_model,
        "messages": [{"role": "user", "content": "Reply with: hi"}],
        "max_tokens": 16,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if not 200 <= response.status_code < 300:
        return ConnTestOutcome(
            False,
            "upstream_error",
            f"上游返回 {response.status_code}",
            response.status_code,
        )
    try:
        reply = _chat_reply_text(response.json())
    except ValueError:
        return ConnTestOutcome(False, "bad_response", "响应不是合法 JSON", response.status_code)
    if not reply.strip():
        return ConnTestOutcome(False, "empty_reply", "模型未返回有效内容", response.status_code)
    return ConnTestOutcome(True, "ok", "ok", response.status_code)


async def test_anthropic(
    *,
    base_url: str,
    api_key: str,
    real_model: str,
    timeout_seconds: float = LLM_CONNECT_TEST_TIMEOUT_SECONDS,
) -> ConnTestOutcome:
    """Anthropic 没有 list_models；最小 messages 请求是连通 + 鉴权的标准做法。"""
    blocked = await _ssrf_precheck(base_url)
    if blocked is not None:
        return blocked
    url = _normalize_anthropic_url(base_url)
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": real_model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply with: hi"}],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if not 200 <= response.status_code < 300:
        return ConnTestOutcome(
            False,
            "upstream_error",
            f"上游返回 {response.status_code}",
            response.status_code,
        )
    try:
        reply = _anthropic_reply_text(response.json())
    except ValueError:
        return ConnTestOutcome(False, "bad_response", "响应不是合法 JSON", response.status_code)
    if not reply.strip():
        return ConnTestOutcome(False, "empty_reply", "模型未返回有效内容", response.status_code)
    return ConnTestOutcome(True, "ok", "ok", response.status_code)


async def test_openai_responses(
    *,
    base_url: str,
    api_key: str,
    real_model: str,
    timeout_seconds: float = LLM_CONNECT_TEST_TIMEOUT_SECONDS,
) -> ConnTestOutcome:
    """OpenAI Responses 无列表端点；用最小真实请求度量连通性。"""
    blocked = await _ssrf_precheck(base_url)
    if blocked is not None:
        return blocked
    url = base_url.rstrip("/") + "/responses"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": real_model, "input": "Reply with: hi", "max_output_tokens": 16}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if not 200 <= response.status_code < 300:
        return ConnTestOutcome(
            False,
            "upstream_error",
            f"上游返回 {response.status_code}",
            response.status_code,
        )
    try:
        reply = _responses_reply_text(response.json())
    except ValueError:
        return ConnTestOutcome(False, "bad_response", "响应不是合法 JSON", response.status_code)
    if not reply.strip():
        return ConnTestOutcome(False, "empty_reply", "模型未返回有效内容", response.status_code)
    return ConnTestOutcome(True, "ok", "ok", response.status_code)


async def run_connectivity_test(
    *,
    protocol: LLMProtocol,
    base_url: str,
    api_key: str,
    real_model: str,
) -> ConnTestOutcome:
    """按协议分发；统一超时上限 10s（独立于配置 timeout_seconds）。"""
    if protocol is LLMProtocol.OPENAI_CHAT:
        outcome = await test_openai_chat(
            base_url=base_url,
            api_key=api_key,
            real_model=real_model,
        )
    elif protocol is LLMProtocol.OPENAI_RESPONSES:
        outcome = await test_openai_responses(
            base_url=base_url,
            api_key=api_key,
            real_model=real_model,
        )
    elif protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        outcome = await test_anthropic(
            base_url=base_url,
            api_key=api_key,
            real_model=real_model,
        )
    else:
        outcome = ConnTestOutcome(False, "unsupported_protocol", f"未知协议: {protocol}", None)
    # 统一入口记录结构化日志；不打 full base_url（可能含内网/IP），只保留 host:port 与协议。
    from urllib.parse import urlparse

    host = urlparse(base_url).netloc or base_url
    log_event(
        "info" if outcome.success else "warning",
        "llm_test.passed" if outcome.success else "llm_test.failed",
        "LLM 连通性测试成功" if outcome.success else "LLM 连通性测试失败",
        protocol=protocol.value,
        host=host,
        real_model=real_model,
        reason_code=outcome.reason_code,
        http_status=outcome.http_status,
        detail=outcome.detail,
    )
    return outcome


def outcome_to_dict(outcome: ConnTestOutcome) -> dict[str, object]:
    return {
        "success": outcome.success,
        "reason_code": outcome.reason_code,
        "detail": outcome.detail,
        "http_status": outcome.http_status,
    }

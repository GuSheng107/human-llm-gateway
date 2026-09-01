"""LLM 配置连通性测试（M7-A，docs/API_CONTRACT.md §6）。

按 LLM 协议分流调用真实 endpoint（仅连通性测试，不保存任何响应正文）：

- `openai_chat`：GET {base_url}/models，使用 `Authorization: Bearer <api_key>`。
- `anthropic`：POST {base_url}/v1/messages，使用 `Authorization: x-api-key` 与
  `anthropic-version: 2023-06-01`，最小请求体（max_tokens=1, messages=[user "."]）。

成功：HTTP 2xx。
失败：超时、网络错误、非 2xx、响应体解析失败 -> 返回 `failed` 与简化的 reason_code，
并把 last_test_result 写入数据库。失败 reason 不回显 Secret。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..core.constants import LLM_CONNECT_TEST_TIMEOUT_SECONDS
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


def _normalize_openai_models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


async def _ssrf_precheck(base_url: str) -> ConnTestOutcome | None:
    """连通性测试前的 SSRF 校验；违规返回失败 outcome（不抛错）。"""
    from starlette.concurrency import run_in_threadpool

    from ..core.ssrf import SsrfViolation, validate_base_url

    try:
        await run_in_threadpool(validate_base_url, base_url)
    except SsrfViolation as exc:
        return ConnTestOutcome(False, "blocked", str(exc), None)
    return None


async def test_openai_chat(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = LLM_CONNECT_TEST_TIMEOUT_SECONDS,
) -> ConnTestOutcome:
    blocked = await _ssrf_precheck(base_url)
    if blocked is not None:
        return blocked
    url = _normalize_openai_models_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if 200 <= response.status_code < 300:
        return ConnTestOutcome(True, "ok", "ok", response.status_code)
    return ConnTestOutcome(
        False,
        "upstream_error",
        f"上游返回 {response.status_code}",
        response.status_code,
    )


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
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if 200 <= response.status_code < 300:
        return ConnTestOutcome(True, "ok", "ok", response.status_code)
    return ConnTestOutcome(
        False,
        "upstream_error",
        f"上游返回 {response.status_code}",
        response.status_code,
    )


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
    body = {"model": real_model, "input": "ping", "max_output_tokens": 4}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return ConnTestOutcome(False, "timeout", "连接超时", None)
    except httpx.RequestError as exc:
        return ConnTestOutcome(False, "network_error", f"网络错误: {exc.__class__.__name__}", None)
    if 200 <= response.status_code < 300:
        return ConnTestOutcome(True, "ok", "ok", response.status_code)
    return ConnTestOutcome(
        False,
        "upstream_error",
        f"上游返回 {response.status_code}",
        response.status_code,
    )


async def run_connectivity_test(
    *,
    protocol: LLMProtocol,
    base_url: str,
    api_key: str,
    real_model: str,
) -> ConnTestOutcome:
    """按协议分发；统一超时上限 10s（独立于配置 timeout_seconds）。"""
    if protocol is LLMProtocol.OPENAI_CHAT:
        return await test_openai_chat(
            base_url=base_url,
            api_key=api_key,
        )
    if protocol is LLMProtocol.OPENAI_RESPONSES:
        return await test_openai_responses(
            base_url=base_url,
            api_key=api_key,
            real_model=real_model,
        )
    if protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        return await test_anthropic(
            base_url=base_url,
            api_key=api_key,
            real_model=real_model,
        )
    return ConnTestOutcome(False, "unsupported_protocol", f"未知协议: {protocol}", None)


def outcome_to_dict(outcome: ConnTestOutcome) -> dict[str, object]:
    return {
        "success": outcome.success,
        "reason_code": outcome.reason_code,
        "detail": outcome.detail,
        "http_status": outcome.http_status,
    }

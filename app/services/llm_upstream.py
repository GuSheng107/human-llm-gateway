"""真实 LLM 上游 HTTP 调用（M7-B 草稿生成 / M7-C 转发共用）。

统一 httpx 非流式调用：OpenAI Chat Completions 与 Anthropic Messages。
错误统一映射为 DomainError（超时 504 / 网络 502 / 非 2xx 502），
不透传上游响应正文（可能包含敏感信息）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..domain.errors import DomainError, DomainErrorCode


async def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for k, v in extra_headers.items():
        if k.lower() not in {"authorization", "content-type"}:
            headers.setdefault(k, v)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        raise DomainError(
            DomainErrorCode.REQUEST_TIMEOUT,
            "上游 LLM 请求超时",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游 LLM 网络错误",
            status_code=502,
        ) from exc
    if resp.status_code >= 400:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            f"上游 LLM 返回 {resp.status_code}",
            status_code=502,
        )
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游 LLM 响应不是合法 JSON",
            status_code=502,
        ) from exc


async def post_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    for k, v in extra_headers.items():
        if k.lower() not in {"x-api-key", "anthropic-version", "content-type"}:
            headers.setdefault(k, v)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=request_body)
    except httpx.TimeoutException as exc:
        raise DomainError(
            DomainErrorCode.REQUEST_TIMEOUT,
            "上游 LLM 请求超时",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游 LLM 网络错误",
            status_code=502,
        ) from exc
    if resp.status_code >= 400:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            f"上游 LLM 返回 {resp.status_code}",
            status_code=502,
        )
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游 LLM 响应不是合法 JSON",
            status_code=502,
        ) from exc

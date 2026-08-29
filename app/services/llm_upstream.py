"""真实 LLM 上游 HTTP 调用（M7-B 草稿生成 / M7-C 转发 / M7-D 流式共用）。

统一 httpx 调用：OpenAI Chat Completions 与 Anthropic Messages，非流式与
SSE 流式两种。错误统一映射为 DomainError（超时 504 / 网络 502 / 非 2xx 502），
不透传上游响应正文（可能包含敏感信息）。

资源与 SSRF 防护：
- 每次请求前重解 base_url 并做 SSRF 分档校验（防 DNS rebinding；经
  run_in_threadpool 调用链执行同步 getaddrinfo）；
- 非流式响应体上限 LLM_MAX_RESPONSE_BYTES；
- 流式累计字节 / 总时长 / 单行长度上限（httpx timeout 只约束单次读写）。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..core.constants import (
    LLM_MAX_RESPONSE_BYTES,
    LLM_MAX_SSE_LINE_BYTES,
    LLM_MAX_STREAM_BYTES,
    LLM_MAX_STREAM_SECONDS,
)
from ..domain.errors import DomainError, DomainErrorCode


class UpstreamChunk:
    """流式增量：text / reasoning / tool_call 片段（协议无关）。"""

    __slots__ = ("reasoning", "text", "tool_call")

    def __init__(
        self,
        *,
        text: str = "",
        reasoning: str = "",
        tool_call: dict[str, Any] | None = None,
    ) -> None:
        self.text = text
        self.reasoning = reasoning
        self.tool_call = tool_call


def _raise_upstream(status_code: int) -> DomainError:
    return DomainError(
        DomainErrorCode.UPSTREAM_ERROR,
        f"上游 LLM 返回 {status_code}",
        status_code=502,
    )


def _raise_timeout() -> DomainError:
    return DomainError(DomainErrorCode.REQUEST_TIMEOUT, "上游 LLM 请求超时", status_code=504)


def _raise_network() -> DomainError:
    return DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游 LLM 网络错误", status_code=502)


def _raise_bad_json() -> DomainError:
    return DomainError(
        DomainErrorCode.UPSTREAM_ERROR, "上游 LLM 响应不是合法 JSON", status_code=502
    )


def _raise_too_large(kind: str) -> DomainError:
    return DomainError(
        DomainErrorCode.UPSTREAM_ERROR,
        f"上游 LLM 响应{kind}超出上限",
        status_code=502,
    )


async def _precheck_ssrf(base_url: str) -> None:
    """请求前 SSRF 分档校验：配置后 DNS 指向可能改变（rebinding）。

    getaddrinfo 是阻塞调用：经 threadpool 执行，不阻塞事件循环。
    """
    from starlette.concurrency import run_in_threadpool

    from ..core.ssrf import SsrfViolation, validate_base_url

    try:
        await run_in_threadpool(validate_base_url, base_url)
    except SsrfViolation as exc:
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, str(exc), status_code=502) from exc


def _chat_headers(api_key: str, extra: dict[str, str]) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for k, v in extra.items():
        if k.lower() not in {"authorization", "content-type"}:
            headers.setdefault(k, v)
    return headers


def _anthropic_messages_url(base_url: str) -> str:
    """Anthropic messages endpoint：兼容已归一（含 /v1）与裸 host 两种形态。

    M7-D 起配置层把 anthropic base_url 归一为含 /v1；历史配置（裸 host）
    在此防御性补齐，避免旧数据升级后 404。
    """
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned + "/messages"
    return cleaned + "/v1/messages"


def _anthropic_headers(api_key: str, extra: dict[str, str]) -> dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    for k, v in extra.items():
        if k.lower() not in {"x-api-key", "anthropic-version", "content-type"}:
            headers.setdefault(k, v)
    return headers


async def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    await _precheck_ssrf(base_url)
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                url, headers=_chat_headers(api_key, extra_headers), json=request_body
            )
    except httpx.TimeoutException as exc:
        raise _raise_timeout() from exc
    except httpx.RequestError as exc:
        raise _raise_network() from exc
    if resp.status_code >= 400:
        raise _raise_upstream(resp.status_code)
    raw = resp.content
    if len(raw) > LLM_MAX_RESPONSE_BYTES:
        raise _raise_too_large("体积")
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _raise_bad_json() from exc


async def post_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    await _precheck_ssrf(base_url)
    url = _anthropic_messages_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                url, headers=_anthropic_headers(api_key, extra_headers), json=request_body
            )
    except httpx.TimeoutException as exc:
        raise _raise_timeout() from exc
    except httpx.RequestError as exc:
        raise _raise_network() from exc
    if resp.status_code >= 400:
        raise _raise_upstream(resp.status_code)
    raw = resp.content
    if len(raw) > LLM_MAX_RESPONSE_BYTES:
        raise _raise_too_large("体积")
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise _raise_bad_json() from exc


# ----------------------------------------------------------------------
# 流式（SSE）
# ----------------------------------------------------------------------


class _StreamBudget:
    """流式资源预算：累计字节 + 总接收时长（httpx timeout 只管单次读写）。"""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.started_at = time.monotonic()

    def charge(self, line: str) -> None:
        self.bytes_read += len(line.encode("utf-8", errors="replace")) + 1
        if self.bytes_read > LLM_MAX_STREAM_BYTES:
            raise _raise_too_large("累计字节")
        if time.monotonic() - self.started_at > LLM_MAX_STREAM_SECONDS:
            raise _raise_timeout()


async def stream_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> AsyncIterator[UpstreamChunk]:
    """流式 Chat Completions：解析 delta.content / reasoning_content /
    tool_calls 增量并归一为 UpstreamChunk。"""
    await _precheck_ssrf(base_url)
    url = base_url.rstrip("/") + "/chat/completions"
    body = {**request_body, "stream": True}
    budget = _StreamBudget()
    client = httpx.AsyncClient(timeout=timeout_seconds)
    try:
        async with client.stream(
            "POST", url, headers=_chat_headers(api_key, extra_headers), json=body
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise _raise_upstream(resp.status_code)
            async for chunk in _iter_sse_data(resp, budget):
                yield _parse_chat_delta(chunk)
    except httpx.TimeoutException as exc:
        raise _raise_timeout() from exc
    except httpx.RequestError as exc:
        raise _raise_network() from exc
    finally:
        await client.aclose()


async def stream_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    extra_headers: dict[str, str],
    timeout_seconds: float,
) -> AsyncIterator[UpstreamChunk]:
    """流式 Anthropic Messages：解析 content_block_delta（text_delta /
    thinking_delta / input_json_delta）并归一为 UpstreamChunk。"""
    await _precheck_ssrf(base_url)
    url = _anthropic_messages_url(base_url)
    body = {**request_body, "stream": True}
    budget = _StreamBudget()
    client = httpx.AsyncClient(timeout=timeout_seconds)
    tool_json_buffers: dict[int, dict[str, str]] = {}
    try:
        async with client.stream(
            "POST", url, headers=_anthropic_headers(api_key, extra_headers), json=body
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise _raise_upstream(resp.status_code)
            async for event in _iter_sse(resp, budget):
                chunk = _parse_anthropic_event(event, tool_json_buffers)
                if chunk is not None:
                    yield chunk
    except httpx.TimeoutException as exc:
        raise _raise_timeout() from exc
    except httpx.RequestError as exc:
        raise _raise_network() from exc
    finally:
        await client.aclose()


async def _iter_sse_data(
    resp: httpx.Response, budget: _StreamBudget
) -> AsyncIterator[dict[str, Any]]:
    """提取 data: JSON 行（Chat 格式：每 data 行一个完整对象）。"""
    async for line in resp.aiter_lines():
        budget.charge(line)
        if len(line.encode("utf-8", errors="replace")) > LLM_MAX_SSE_LINE_BYTES:
            raise _raise_too_large("单行")
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except ValueError:
            _log_bad_sse_line(payload)


async def _iter_sse(
    resp: httpx.Response, budget: _StreamBudget
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """提取 (event, data) 对（Anthropic 格式：event: + data: 成对）。"""
    event_name = ""
    async for line in resp.aiter_lines():
        budget.charge(line)
        if len(line.encode("utf-8", errors="replace")) > LLM_MAX_SSE_LINE_BYTES:
            raise _raise_too_large("单行")
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                yield event_name, json.loads(payload)
            except ValueError:
                _log_bad_sse_line(payload)
                continue
            event_name = ""


def _log_bad_sse_line(payload: str) -> None:
    """SSE 坏行告警（截断采样，线上排障必需；不整行落日志防放大）。"""
    import logging

    logging.getLogger("app.services.llm_upstream").warning(
        "上游 SSE 行不是合法 JSON（已跳过）: %s...", payload[:80]
    )


def _parse_chat_delta(payload: dict[str, Any]) -> UpstreamChunk:
    choices = payload.get("choices") or []
    if not choices:
        return UpstreamChunk()
    delta = choices[0].get("delta") or {}
    text = delta.get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    tool_call: dict[str, Any] | None = None
    calls = delta.get("tool_calls")
    if isinstance(calls, list) and calls:
        first = calls[0]
        if isinstance(first, dict):
            fn = first.get("function") or {}
            if first.get("id") and fn.get("name"):
                # 新调用开始：携带完整 id/name（arguments 后续以 index 增量）
                tool_call = {
                    "id": first.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments_delta": fn.get("arguments") or "",
                }
            elif fn.get("arguments"):
                tool_call = {"arguments_delta": fn.get("arguments") or ""}
    return UpstreamChunk(text=text, reasoning=reasoning, tool_call=tool_call)


def _parse_anthropic_event(
    event: str, payload: dict[str, Any], tool_buffers: dict[int, dict[str, str]]
) -> UpstreamChunk | None:
    etype = payload.get("type") or event
    if etype == "content_block_delta":
        delta = payload.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            return UpstreamChunk(text=delta.get("text", ""))
        if dtype == "thinking_delta":
            return UpstreamChunk(reasoning=delta.get("thinking", ""))
        if dtype == "input_json_delta":
            index = payload.get("index", 0)
            buf = tool_buffers.setdefault(index, {"json": ""})
            buf["json"] += delta.get("partial_json", "")
            return None
        return None
    if etype == "content_block_start":
        block = payload.get("content_block") or {}
        index = payload.get("index", 0)
        if block.get("type") == "tool_use":
            tool_buffers[index] = {
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "json": "",
            }
        return None
    if etype == "content_block_stop":
        index = payload.get("index", 0)
        buf = tool_buffers.pop(index, None)
        if buf is None:
            return None
        try:
            arguments = json.loads(buf["json"]) if buf["json"].strip() else {}
        except ValueError:
            arguments = {}
        return UpstreamChunk(
            tool_call={"id": buf.get("id", ""), "name": buf.get("name", ""), "arguments": arguments}
        )
    return None


def collect_chunk(target: dict[str, Any], chunk: UpstreamChunk) -> None:
    """把增量 chunk 累积到 target（text/reasoning/tool_calls）。

    Chat 形态：首个 tool_call chunk 带 id/name，后续仅 arguments 增量。
    Anthropic 形态：content_block_stop 一次性给出完整 arguments。
    """
    if chunk.text:
        target["text"] = target.get("text", "") + chunk.text
    if chunk.reasoning:
        target["reasoning"] = target.get("reasoning", "") + chunk.reasoning
    if chunk.tool_call:
        call = chunk.tool_call
        calls = target.setdefault("tool_calls", [])
        if "arguments" in call:
            # 完整调用（Anthropic content_block_stop / 已聚合形态）
            calls.append(
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments": call["arguments"],
                }
            )
        elif "id" in call:
            # Chat 新调用开始：记录 id/name 并开始累积 arguments
            calls.append(
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments_raw": call.get("arguments_delta", ""),
                }
            )
        elif "arguments_delta" in call:
            delta = call["arguments_delta"]
            if calls and "arguments_raw" in calls[-1]:
                calls[-1]["arguments_raw"] += delta
            else:
                target["arguments_raw_head"] = target.get("arguments_raw_head", "") + delta


def finalize_collected(target: dict[str, Any]) -> dict[str, Any]:
    """累积结果 -> 协议无关摘要（与 ReplyDraft 字段对齐）。"""
    tool_calls: list[dict[str, Any]] = []
    for call in target.get("tool_calls", []):
        raw = call.get("arguments_raw") or "{}"
        try:
            arguments = json.loads(raw) if raw.strip() else {}
        except ValueError:
            arguments = {}
        tool_calls.append(
            {"id": call.get("id", ""), "name": call.get("name", ""), "arguments": arguments}
        )
    if not tool_calls and target.get("arguments_raw_head"):
        raw = target["arguments_raw_head"]
        try:
            arguments = json.loads(raw) if raw.strip() else {}
        except ValueError:
            arguments = {}
        tool_calls.append({"id": "", "name": "", "arguments": arguments})
    return {
        "reasoning": target.get("reasoning") or None,
        "tool_calls": tool_calls,
        "final_text": target.get("text") or None,
    }

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

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

    __slots__ = ("reasoning", "status_code", "text", "tool_call")

    def __init__(
        self,
        *,
        text: str = "",
        reasoning: str = "",
        tool_call: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.text = text
        self.reasoning = reasoning
        self.tool_call = tool_call
        self.status_code = status_code


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


def _raise_incomplete_stream() -> DomainError:
    return DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游 LLM 流未正常完成", status_code=502)


def _parse_tool_arguments(value: object) -> dict[str, Any]:
    """只接受完整参数对象，不能把损坏的参数修复成合法空对象。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise _raise_bad_json() from exc
    if not isinstance(value, dict):
        raise _raise_bad_json()
    return value


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


def _chat_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _responses_url(base_url: str) -> str:
    """OpenAI Responses endpoint：与 chat 相同的 base_url 归一规则（去掉路径尾 "/v1" 后追加 /responses）。

    用户填到 /v1（或不带 v1 的裸 host）均可，这里统一拼 /responses。
    """
    cleaned = base_url.rstrip("/")
    cleaned = cleaned.removesuffix("/chat/completions").removesuffix("/responses")
    return cleaned + "/responses"


def _chat_completions_url(base_url: str) -> str:
    """接受 API base URL 或完整端点，且不重复拼接路径。"""
    cleaned = base_url.rstrip("/")
    cleaned = cleaned.removesuffix("/responses").removesuffix("/chat/completions")
    return cleaned + "/chat/completions"


def _anthropic_messages_url(base_url: str) -> str:
    """Anthropic messages endpoint：兼容已归一（含 /v1）与裸 host 两种形态。

    M7-D 起配置层把 anthropic base_url 归一为含 /v1；历史配置（裸 host）
    在此防御性补齐，避免旧数据升级后 404。
    """
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1/messages"):
        return cleaned
    if cleaned.endswith("/v1"):
        return cleaned + "/messages"
    return cleaned + "/v1/messages"


def endpoint_label(base_url: str, protocol: object) -> str:
    """Return the resolved upstream host/path without query strings or credentials."""
    parsed_base = urlsplit(base_url)
    clean_base_url = urlunsplit(
        (
            parsed_base.scheme,
            parsed_base.netloc,
            parsed_base.path.rstrip("/"),
            "",
            "",
        )
    )
    protocol_value = getattr(protocol, "value", protocol)
    if protocol_value == "openai_responses":
        endpoint = _responses_url(clean_base_url)
    elif protocol_value == "anthropic_messages":
        endpoint = _anthropic_messages_url(clean_base_url)
    else:
        endpoint = _chat_completions_url(clean_base_url)
    parsed = urlsplit(endpoint)
    if parsed.netloc:
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is not None:
            host = f"{host}:{port}"
        return f"{parsed.scheme}://{host}{parsed.path}"
    return parsed.path or parsed.netloc


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


@asynccontextmanager
async def _upstream_response(
    *,
    base_url: str,
    url: str,
    headers: dict[str, str],
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> AsyncIterator[httpx.Response]:
    """共享地址检查和总预算；不读取错误正文，不跟随重定向。"""
    try:
        async with asyncio.timeout(LLM_MAX_STREAM_SECONDS):
            await _precheck_ssrf(base_url)
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                client.follow_redirects = False
                async with client.stream("POST", url, headers=headers, json=request_body) as resp:
                    if not 200 <= resp.status_code < 300:
                        raise _raise_upstream(resp.status_code)
                    yield resp
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise _raise_timeout() from exc
    except httpx.RequestError as exc:
        raise _raise_network() from exc


async def _post_json(
    *,
    base_url: str,
    url: str,
    headers: dict[str, str],
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    async with _upstream_response(
        base_url=base_url,
        url=url,
        headers=headers,
        request_body=request_body,
        timeout_seconds=timeout_seconds,
    ) as resp:
        raw = bytearray()
        async for chunk in resp.aiter_bytes():
            if len(raw) + len(chunk) > LLM_MAX_RESPONSE_BYTES:
                raise _raise_too_large("体积")
            raw.extend(chunk)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise _raise_bad_json() from exc
        if not isinstance(payload, dict):
            raise _raise_bad_json()
        return payload


async def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return await _post_json(
        base_url=base_url,
        url=_chat_completions_url(base_url),
        headers=_chat_headers(api_key),
        request_body=request_body,
        timeout_seconds=timeout_seconds,
    )


async def post_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return await _post_json(
        base_url=base_url,
        url=_anthropic_messages_url(base_url),
        headers=_anthropic_headers(api_key),
        request_body=request_body,
        timeout_seconds=timeout_seconds,
    )


# ----------------------------------------------------------------------
# 流式（SSE）
# ----------------------------------------------------------------------


class _StreamBudget:
    """流式资源预算：累计字节 + 总接收时长（httpx timeout 只管单次读写）。"""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.started_at = time.monotonic()

    def charge(self, chunk: bytes | str) -> None:
        self.bytes_read += (
            len(chunk) if isinstance(chunk, bytes) else len(chunk.encode("utf-8")) + 1
        )
        if self.bytes_read > LLM_MAX_STREAM_BYTES:
            raise _raise_too_large("累计字节")
        if time.monotonic() - self.started_at > LLM_MAX_STREAM_SECONDS:
            raise _raise_timeout()


async def post_responses(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """OpenAI Responses：POST {base_url}/responses。"""
    return await _post_json(
        base_url=base_url,
        url=_responses_url(base_url),
        headers=_chat_headers(api_key),
        request_body=request_body,
        timeout_seconds=timeout_seconds,
    )


async def stream_chat_completions(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> AsyncIterator[UpstreamChunk]:
    """流式 Chat Completions：解析 delta.content / reasoning_content /
    tool_calls 增量并归一为 UpstreamChunk。"""
    budget = _StreamBudget()
    async with _upstream_response(
        base_url=base_url,
        url=_chat_completions_url(base_url),
        headers=_chat_headers(api_key),
        request_body={**request_body, "stream": True},
        timeout_seconds=timeout_seconds,
    ) as resp:
        async for chunk in _iter_sse_data(resp, budget):
            if chunk is None:  # Chat 的 data: [DONE]
                return
            if "error" in chunk or chunk.get("type") == "error":
                raise _raise_incomplete_stream()
            for parsed in _parse_chat_delta(chunk):
                parsed.status_code = resp.status_code
                yield parsed
        raise _raise_incomplete_stream()


async def stream_responses(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> AsyncIterator[UpstreamChunk]:
    """流式 OpenAI Responses：解析 response.output_text.delta /
    response.reasoning_summary_text.delta / response.function_call_* 事件。"""
    budget = _StreamBudget()
    async with _upstream_response(
        base_url=base_url,
        url=_responses_url(base_url),
        headers=_chat_headers(api_key),
        request_body={**request_body, "stream": True},
        timeout_seconds=timeout_seconds,
    ) as resp:
        async for chunk in _iter_sse_data(resp, budget):
            if chunk is None:  # Responses 不使用 Chat 的 [DONE]。
                raise _raise_incomplete_stream()
            if chunk.get("type") == "response.completed":
                if (chunk.get("response") or {}).get("status") != "completed":
                    raise _raise_incomplete_stream()
                return
            parsed = _parse_responses_event(chunk)
            if parsed is not None:
                parsed.status_code = resp.status_code
                yield parsed
        raise _raise_incomplete_stream()


def _parse_responses_event(payload: dict[str, Any]) -> UpstreamChunk | None:
    """Responses SSE 内容归一；失败事件不得作为可忽略的通知。"""
    event_type = payload.get("type") or ""
    if event_type in ("error", "response.failed", "response.incomplete") or "error" in payload:
        raise _raise_incomplete_stream()
    if event_type == "response.output_text.delta":
        return UpstreamChunk(text=payload.get("delta") or "")
    if event_type in ("response.reasoning_summary_text.delta", "response.reasoning.delta"):
        return UpstreamChunk(reasoning=payload.get("delta") or "")
    if event_type == "response.function_call_arguments.delta":
        # 参数增量无需消费：response.output_item.done 携带完整 arguments。
        return None
    if event_type == "response.output_item.done":
        item = payload.get("item") or {}
        if item.get("type") == "function_call":
            return UpstreamChunk(
                tool_call={
                    "id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": _parse_tool_arguments(item.get("arguments")),
                }
            )
        return None
    return None


async def stream_anthropic_messages(
    *,
    base_url: str,
    api_key: str,
    request_body: dict[str, Any],
    timeout_seconds: float,
) -> AsyncIterator[UpstreamChunk]:
    """流式 Anthropic Messages：解析 content_block_delta（text_delta /
    thinking_delta / input_json_delta）并归一为 UpstreamChunk。"""
    budget = _StreamBudget()
    tool_json_buffers: dict[int, dict[str, str]] = {}
    async with _upstream_response(
        base_url=base_url,
        url=_anthropic_messages_url(base_url),
        headers=_anthropic_headers(api_key),
        request_body={**request_body, "stream": True},
        timeout_seconds=timeout_seconds,
    ) as resp:
        async for event, payload in _iter_sse(resp, budget):
            if payload is None:
                raise _raise_incomplete_stream()
            if (payload.get("type") or event) == "message_stop":
                if tool_json_buffers:
                    raise _raise_incomplete_stream()
                return
            chunk = _parse_anthropic_event(event, payload, tool_json_buffers)
            if chunk is not None:
                chunk.status_code = resp.status_code
                yield chunk
        raise _raise_incomplete_stream()


async def _iter_sse_data(
    resp: httpx.Response, budget: _StreamBudget
) -> AsyncIterator[dict[str, Any] | None]:
    """读取完整 SSE 数据；None 仅表示 Chat 的 [DONE] 标记。"""
    async for _event, payload in _iter_sse(resp, budget):
        yield payload


def _decode_sse_event(event_name: str, data_lines: list[str]) -> tuple[str, dict[str, Any] | None]:
    """拼接多行 data 并解析；坏 JSON 与非法结构都显式失败，不记录原文。"""
    raw = "\n".join(data_lines)
    if raw.strip() == "[DONE]":
        return event_name, None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise _raise_bad_json() from exc
    if not isinstance(payload, dict):
        raise _raise_bad_json()
    return event_name, payload


async def _bounded_sse_lines(resp: httpx.Response, budget: _StreamBudget) -> AsyncIterator[str]:
    """按接收块计费，在等待换行前限制缓冲；支持 LF、CRLF 和 CR。"""
    pending = bytearray()
    skip_lf = False
    async for chunk in resp.aiter_bytes():
        budget.charge(chunk)
        # 一次只保留单行；无换行的上游不能先无限缓存再校验。
        for byte in chunk:
            if skip_lf:
                skip_lf = False
                if byte == 10:
                    continue
            if byte in (10, 13):
                yield pending.decode("utf-8", errors="replace")
                pending.clear()
                skip_lf = byte == 13
            else:
                pending.append(byte)
                if len(pending) > LLM_MAX_SSE_LINE_BYTES:
                    raise _raise_too_large("单行")
    if pending:
        yield pending.decode("utf-8", errors="replace")


async def _iter_sse(
    resp: httpx.Response, budget: _StreamBudget
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """按空行分隔 SSE 事件，拼接多行 data，拒绝坏 JSON 而不记录原文。

    规范要求事件以空行结束，但部分上游会省略最后一帧的分隔空行。流结束时
    残留的数据只要 JSON 完整就照常产出——成败交给调用方按终止事件判定
    （缺少终止事件仍会在各自 stream_* 的流结束处报失败）；真正被截断的事件
    因 JSON 残缺而在本函数失败。
    """
    event_name = ""
    data_lines: list[str] = []
    async for line in _bounded_sse_lines(resp, budget):
        if not line:
            if data_lines:
                yield _decode_sse_event(event_name, data_lines)
            event_name = ""
            data_lines = []
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
    if data_lines:
        yield _decode_sse_event(event_name, data_lines)


def _parse_chat_delta(payload: dict[str, Any]) -> Iterator[UpstreamChunk]:
    """一帧可有多个工具：正文只产出一次，各工具携带自己的上游 index。"""
    choices = payload.get("choices") or []
    if not choices:
        return
    delta = choices[0].get("delta") or {}
    text = delta.get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    if text or reasoning:
        yield UpstreamChunk(text=text, reasoning=reasoning)
    calls = delta.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                raise _raise_bad_json()
            fn = call.get("function") or {}
            tool_call = {
                "index": call.get("index", 0),
                "arguments_delta": fn.get("arguments") or "",
            }
            if call.get("id"):
                tool_call["id"] = call["id"]
            if fn.get("name"):
                tool_call["name"] = fn["name"]
            yield UpstreamChunk(tool_call=tool_call)


def _parse_anthropic_event(
    event: str, payload: dict[str, Any], tool_buffers: dict[int, dict[str, str]]
) -> UpstreamChunk | None:
    etype = payload.get("type") or event
    if etype == "error" or "error" in payload:
        raise _raise_incomplete_stream()
    if etype == "content_block_delta":
        delta = payload.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            return UpstreamChunk(text=delta.get("text", ""))
        if dtype == "thinking_delta":
            return UpstreamChunk(reasoning=delta.get("thinking", ""))
        if dtype == "input_json_delta":
            index = payload.get("index", 0)
            buf = tool_buffers.get(index)
            if buf is None:
                raise _raise_incomplete_stream()
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
                "initial_json": json.dumps(block.get("input", {})),
            }
        return None
    if etype == "content_block_stop":
        index = payload.get("index", 0)
        buf = tool_buffers.pop(index, None)
        if buf is None:
            return None
        arguments = _parse_tool_arguments(buf["json"] or buf["initial_json"])
        return UpstreamChunk(
            tool_call={"id": buf.get("id", ""), "name": buf.get("name", ""), "arguments": arguments}
        )
    return None


def collect_chunk(target: dict[str, Any], chunk: UpstreamChunk) -> None:
    """把增量 chunk 累积到 target（text/reasoning/tool_calls）。

    Chat 形态：首个 tool_call chunk 带 index/id/name，后续按 index 增量
    （上游并行发起多个调用时 arguments 不串位）。
    Anthropic 形态：content_block_stop 一次性给出完整 arguments。
    无 index 的增量（理论上不存在；防御）回退到最近一个调用。
    """
    if chunk.text:
        target["text"] = target.get("text", "") + chunk.text
    if chunk.reasoning:
        target["reasoning"] = target.get("reasoning", "") + chunk.reasoning
    if chunk.tool_call:
        call = chunk.tool_call
        calls = target.setdefault("tool_calls", [])
        index_by_position: dict[int, int] = target.setdefault("_tc_index", {})
        if "arguments" in call:
            # 完整调用（Anthropic content_block_stop / 已聚合形态）
            calls.append(
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments": call["arguments"],
                }
            )
            return
        if "id" in call:
            # Chat 新调用开始：记录 index 映射并开始累积 arguments
            index_by_position[call.get("index", 0)] = len(calls)
            calls.append(
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments_raw": call.get("arguments_delta", ""),
                }
            )
            return
        if "arguments_delta" in call:
            delta = call["arguments_delta"]
            position = index_by_position.get(call.get("index"))
            if position is None:
                # 无 index 且无映射：回退到最后一个进行中的调用。
                if calls and "arguments_raw" in calls[-1]:
                    position = len(calls) - 1
                else:
                    target["arguments_raw_head"] = target.get("arguments_raw_head", "") + delta
                    return
            calls[position]["arguments_raw"] = calls[position].get("arguments_raw", "") + delta


def finalize_collected(target: dict[str, Any]) -> dict[str, Any]:
    """累积结果 -> 协议无关摘要（与 ReplyDraft 字段对齐；丢弃内部索引）。"""
    tool_calls: list[dict[str, Any]] = []
    for call in target.get("tool_calls", []):
        arguments = _parse_tool_arguments(
            call["arguments"] if "arguments" in call else call.get("arguments_raw", "")
        )
        tool_calls.append(
            {"id": call.get("id", ""), "name": call.get("name", ""), "arguments": arguments}
        )
    if not tool_calls and target.get("arguments_raw_head"):
        arguments = _parse_tool_arguments(target["arguments_raw_head"])
        tool_calls.append({"id": "", "name": "", "arguments": arguments})
    return {
        "reasoning": target.get("reasoning") or None,
        "tool_calls": tool_calls,
        "final_text": target.get("text") or None,
    }

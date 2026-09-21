"""上游 SSE 回归：使用真实 HTTP 解析边界，不替换 stream_* 或解析器。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.db as database
from app.domain.enums import TaskState
from app.domain.errors import DomainError, DomainErrorCode
from app.repositories.models import RequestTask, User
from app.services import llm_upstream

STREAMS = {
    "openai_chat": llm_upstream.stream_chat_completions,
    "openai_responses": llm_upstream.stream_responses,
    "anthropic_messages": llm_upstream.stream_anthropic_messages,
}


def _sse(payload: dict[str, object], event: str = "") -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat(delta: dict[str, object], finish: str | None = None) -> str:
    return _sse({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


def _end(protocol: str) -> str:
    if protocol == "openai_chat":
        return _chat({}, "stop") + "data: [DONE]\n\n"
    if protocol == "openai_responses":
        return _sse({"type": "response.completed", "response": {"status": "completed"}})
    return _sse({"type": "message_stop"}, "message_stop")


def _text(protocol: str, text: str = "部分回复") -> str:
    if protocol == "openai_chat":
        return _chat({"content": text})
    if protocol == "openai_responses":
        return _sse({"type": "response.output_text.delta", "delta": text})
    return _sse(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        "content_block_delta",
    )


def _tool_stream(protocol: str, arguments: str = '{"city":"北京"}') -> str:
    if protocol == "openai_chat":
        return _chat(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_weather",
                        "type": "function",
                        "function": {"name": "weather", "arguments": arguments},
                    }
                ]
            }
        ) + _end(protocol)
    if protocol == "openai_responses":
        return _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "fc_item",
                    "call_id": "call_weather",
                    "name": "weather",
                    "arguments": arguments,
                    "status": "completed",
                },
            }
        ) + _end(protocol)
    return (
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_weather",
                    "name": "weather",
                    "input": {},
                },
            },
            "content_block_start",
        )
        + _sse(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": arguments[:7]},
            },
            "content_block_delta",
        )
        + _sse(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": arguments[7:]},
            },
            "content_block_delta",
        )
        + _sse({"type": "content_block_stop", "index": 0}, "content_block_stop")
        + _end(protocol)
    )


class FragmentedSSE(httpx.AsyncByteStream):
    """刻意切开 UTF-8 字符与 SSE 行，验证 HTTP 读取后的重组。"""

    def __init__(self, wire: str) -> None:
        self.data = wire.encode()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.data), 7):
            yield self.data[offset : offset + 7]

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def serve_sse(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], FragmentedSSE]:
    real_client = httpx.AsyncClient

    def install(wire: str) -> FragmentedSSE:
        stream = FragmentedSSE(wire)

        def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "upstream.example.com"
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        def client(*, timeout: float) -> httpx.AsyncClient:
            return real_client(transport=httpx.MockTransport(respond), timeout=timeout)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        return stream

    return install


async def _collect(protocol: str) -> dict[str, object]:
    collected = {}
    async for chunk in STREAMS[protocol](
        base_url="https://upstream.example.com/v1",
        api_key="test-only-not-a-real-secret",
        request_body={"model": "test-model"},
        timeout_seconds=5,
    ):
        assert chunk.status_code == 200
        llm_upstream.collect_chunk(collected, chunk)
    return llm_upstream.finalize_collected(collected)


@pytest.mark.parametrize("protocol", STREAMS)
async def test_stream_text_and_terminal(protocol: str, serve_sse: Callable) -> None:
    stream = serve_sse(_text(protocol) + _end(protocol))
    assert await _collect(protocol) == {
        "reasoning": None,
        "tool_calls": [],
        "final_text": "部分回复",
    }
    assert stream.closed


@pytest.mark.parametrize("protocol", STREAMS)
@pytest.mark.parametrize(
    "arguments", ['{"city":"北京"}', "{}", '{"nested":{"ids":[1,2],"ok":true}}']
)
async def test_tool_only_reply_preserves_arguments(
    protocol: str, arguments: str, serve_sse: Callable
) -> None:
    stream = serve_sse(_tool_stream(protocol, arguments))
    result = await _collect(protocol)
    assert result == {
        "reasoning": None,
        "final_text": None,
        "tool_calls": [
            {"id": "call_weather", "name": "weather", "arguments": json.loads(arguments)}
        ],
    }
    assert stream.closed


async def test_chat_multiple_tools_in_one_delta_and_interleaved_fragments(
    serve_sse: Callable,
) -> None:
    wire = _chat(
        {
            "content": "一次正文",
            "reasoning_content": "一次思考",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_0",
                    "function": {"name": "weather", "arguments": '{"city":'},
                },
                {"index": 1, "id": "call_1", "function": {"name": "lookup", "arguments": '{"id":'}},
            ],
        }
    )
    wire += _chat(
        {
            "tool_calls": [
                {"index": 1, "function": {"arguments": "42}"}},
                {"index": 0, "function": {"arguments": '"北京"}'}},
            ]
        }
    ) + _end("openai_chat")
    serve_sse(wire)
    assert await _collect("openai_chat") == {
        "reasoning": "一次思考",
        "final_text": "一次正文",
        "tool_calls": [
            {"id": "call_0", "name": "weather", "arguments": {"city": "北京"}},
            {"id": "call_1", "name": "lookup", "arguments": {"id": 42}},
        ],
    }


@pytest.mark.parametrize("protocol", STREAMS)
@pytest.mark.parametrize("arguments", ['{"city":', "[1,2]", "null"])
async def test_invalid_tool_arguments_are_not_replaced_with_empty_object(
    protocol: str,
    arguments: str,
    serve_sse: Callable,
) -> None:
    serve_sse(_tool_stream(protocol, arguments))
    with pytest.raises(DomainError) as exc:
        await _collect(protocol)
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


@pytest.mark.parametrize("protocol", STREAMS)
@pytest.mark.parametrize("failure", ["error", "eof", "bad_json", "non_object"])
async def test_failed_stream_never_finishes_as_success(
    protocol: str,
    failure: str,
    serve_sse: Callable,
    caplog: pytest.LogCaptureFixture,
) -> None:
    wire = _text(protocol)
    if failure == "error":
        wire += _sse({"type": "error", "error": {"message": "PRIVATE_UPSTREAM_TEXT"}}, "error")
        wire += _end(protocol)
    elif failure == "bad_json":
        wire += "data: PRIVATE_UPSTREAM_TEXT{\n\n" + _end(protocol)
    elif failure == "non_object":
        wire += "data: []\n\n" + _end(protocol)
    stream = serve_sse(wire)
    with pytest.raises(DomainError) as exc:
        await _collect(protocol)
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR
    assert "PRIVATE_UPSTREAM_TEXT" not in str(exc.value)
    assert "PRIVATE_UPSTREAM_TEXT" not in caplog.text
    assert stream.closed


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete"])
async def test_responses_failure_terminal_rejects_partial_reply(
    terminal: str, serve_sse: Callable
) -> None:
    serve_sse(
        _text("openai_responses")
        + _sse({"type": terminal, "response": {"status": terminal.split(".")[1]}})
    )
    with pytest.raises(DomainError) as exc:
        await _collect("openai_responses")
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


@pytest.mark.parametrize("protocol", STREAMS)
async def test_comments_multiline_data_and_crlf(protocol: str, serve_sse: Callable) -> None:
    wire = ': keepalive\n\nevent: ping\ndata: {\ndata: "type": "ping"\ndata: }\n\n'
    wire += _text(protocol) + _end(protocol)
    serve_sse(wire.replace("\n", "\r\n"))
    assert (await _collect(protocol))["final_text"] == "部分回复"


@pytest.mark.parametrize("protocol", STREAMS)
async def test_missing_trailing_blank_line_still_succeeds(
    protocol: str, serve_sse: Callable
) -> None:
    """上游省略末帧分隔空行但事件完整：终止事件仍应被正常消费。"""
    stream = serve_sse((_text(protocol) + _end(protocol)).rstrip("\n"))
    assert (await _collect(protocol))["final_text"] == "部分回复"
    assert stream.closed


@pytest.mark.parametrize("protocol", STREAMS)
async def test_truncated_final_event_is_not_success(protocol: str, serve_sse: Callable) -> None:
    """末帧 JSON 被截断：残缺数据不得被当成完整事件接受。"""
    serve_sse((_text(protocol) + _end(protocol)).rstrip("\n")[:-1])
    with pytest.raises(DomainError):
        await _collect(protocol)


@pytest.mark.parametrize("protocol", ["openai_responses", "anthropic_messages"])
async def test_multiple_complete_tools_and_reasoning(protocol: str, serve_sse: Callable) -> None:
    tools = _tool_stream(protocol).removesuffix(_end(protocol))
    second = tools.replace("call_weather", "call_next").replace("fc_item", "fc_next")
    second = second.replace('"weather"', '"lookup"').replace("北京", "上海")
    if protocol == "openai_responses":
        reasoning = _sse({"type": "response.reasoning_summary_text.delta", "delta": "思考"})
    else:
        reasoning = _sse(
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "thinking_delta", "thinking": "思考"},
            },
            "content_block_delta",
        )
    serve_sse(reasoning + _text(protocol) + tools + second + _end(protocol))
    assert await _collect(protocol) == {
        "reasoning": "思考",
        "final_text": "部分回复",
        "tool_calls": [
            {"id": "call_weather", "name": "weather", "arguments": {"city": "北京"}},
            {"id": "call_next", "name": "lookup", "arguments": {"city": "上海"}},
        ],
    }


async def test_anthropic_unclosed_tool_block_is_not_success(serve_sse: Callable) -> None:
    wire = _tool_stream("anthropic_messages").replace(
        _sse({"type": "content_block_stop", "index": 0}, "content_block_stop"), ""
    )
    serve_sse(wire)
    with pytest.raises(DomainError):
        await _collect("anthropic_messages")


@pytest.mark.parametrize("arguments", [{}, {"city": "北京"}])
async def test_anthropic_initial_tool_input_without_deltas(
    arguments: dict[str, object], serve_sse: Callable
) -> None:
    wire = _sse(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "call_weather",
                "name": "weather",
                "input": arguments,
            },
        }
    )
    wire += _sse({"type": "content_block_stop", "index": 0}) + _end("anthropic_messages")
    serve_sse(wire)
    assert (await _collect("anthropic_messages"))["tool_calls"] == [
        {"id": "call_weather", "name": "weather", "arguments": arguments}
    ]


@pytest.mark.parametrize("protocol", STREAMS)
@pytest.mark.parametrize("failure", ["error", "eof", "invalid_arguments", "success"])
def test_stream_result_and_task_terminal_match(
    client: TestClient,
    created_user: SimpleNamespace,
    serve_sse: Callable,
    protocol: str,
    failure: str,
) -> None:
    cfg = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "stream-regression",
            "protocol": protocol,
            "base_url": "https://upstream.example.com/v1",
            "api_key": "test-only-not-a-real-secret",
            "model": "test-model",
            "timeout_seconds": 5,
            "enabled": True,
        },
    )
    assert cfg.status_code == 201, cfg.text
    key = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={
            "name": "stream-regression",
            "delivery_mode": "web",
            "reply_strategy": "llm",
            "llm_config_id": cfg.json()["id"],
        },
    )
    assert key.status_code == 201, key.text
    payload = {"model": "deepseek-v4-pro", "stream": True}
    if protocol == "openai_responses":
        path = "/v1/responses"
        payload["input"] = "查询天气"
        payload["tools"] = [
            {"type": "function", "name": "weather", "parameters": {"type": "object"}}
        ]
    else:
        payload["messages"] = [{"role": "user", "content": "查询天气"}]
        if protocol == "openai_chat":
            path = "/v1/chat/completions"
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": "weather", "parameters": {"type": "object"}},
                }
            ]
        else:
            path = "/v1/messages"
            payload["max_tokens"] = 64
            payload["tools"] = [{"name": "weather", "input_schema": {"type": "object"}}]
    wire = _text(protocol)
    if failure == "error":
        wire += _sse({"type": "error", "error": {"message": "PRIVATE_UPSTREAM_TEXT"}}, "error")
    elif failure == "invalid_arguments":
        wire += _tool_stream(protocol, '{"city":')
    elif failure == "success":
        wire = _tool_stream(protocol)
    stream = serve_sse(wire)
    response = client.post(
        path, headers={"Authorization": f"Bearer {key.json()['plaintext']}"}, json=payload
    )
    assert response.status_code == (200 if failure == "success" else 500), response.text
    assert "PRIVATE_UPSTREAM_TEXT" not in response.text
    assert "部分回复" not in response.text
    with database.SessionLocal() as session:
        task = session.scalars(
            select(RequestTask).where(RequestTask.api_key_id == int(key.json()["id"]))
        ).one()
        assert json.loads(task.raw_payload_json) == payload
        if failure == "success":
            assert task.state is TaskState.COMPLETED
            assert json.loads(task.response_payload_json)["tool_calls"][0]["arguments"] == {
                "city": "北京"
            }
            assert "deepseek-v4-pro" in response.text
        else:
            assert task.state is TaskState.FAILED
            assert task.response_payload_json is None
        assert task.slot_released_at is not None
        assert session.get(User, created_user.user_id).active_task_count == 0
    assert stream.closed

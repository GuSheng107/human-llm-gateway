"""人工回复 → Chat Completions 协议契约快照测试（M14 §八）。

同一 ReplyDraft（reasoning + 1 个 tool_call + final_text）对同一归一化
context 走完非流式 + 流式，结构必须与文档和官方 SDK 兼容。
"""

from __future__ import annotations

import json

import pytest

from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import chat_completions as chat_protocol

USAGE = {"prompt_tokens": 31, "completion_tokens": 11, "total_tokens": 42}

REPLY = ReplyDraft(
    reasoning="想一下天气",
    tool_calls=[ReplyToolCall(id="call_a1", name="get_weather", arguments={"city": "北京"})],
    final_text="北京今天晴，23°C。",
)

MODEL = "deepseek-v4-pro"


def test_non_stream_shape() -> None:
    body = chat_protocol.render_response(MODEL, REPLY, usage=USAGE)
    assert body["object"] == "chat.completion"
    assert body["model"] == MODEL
    assert isinstance(body["id"], str) and body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["role"] == "assistant"
    assert message["content"] == REPLY.final_text
    assert message["reasoning_content"] == REPLY.reasoning
    assert message["tool_calls"][0]["id"] == "call_a1"
    assert message["tool_calls"][0]["type"] == "function"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"}
    assert body["usage"] == USAGE


def test_stream_order_and_finish_done() -> None:
    frames = list(chat_protocol.stream_frames(MODEL, REPLY, usage=USAGE, include_usage=True))
    assert frames[-1].data == "[DONE]"

    payloads = [json.loads(frame.data) for frame in frames[:-1]]
    # usage 帧（choices 为空）不参与 choices 断言，先分出来
    choice_frames = [item for item in payloads if item["choices"]]
    usage_frames = [item for item in payloads if item["choices"] == []]

    ids = {item["id"] for item in payloads}
    created = {item["created"] for item in payloads}
    assert len(ids) == 1 and len(created) == 1

    first = choice_frames[0]
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    assert any("reasoning_content" in item["choices"][0]["delta"] for item in choice_frames)
    assert any("tool_calls" in item["choices"][0]["delta"] for item in choice_frames)
    text_deltas = [
        item["choices"][0]["delta"]["content"]
        for item in choice_frames
        if item["choices"][0].get("delta", {}).get("content")
    ]
    assert "".join(text_deltas) == REPLY.final_text
    # 结束前最后一帧带 finish_reason
    assert choice_frames[-1]["choices"][0]["finish_reason"] == "tool_calls"
    # include_usage -> choices 为空但带 usage 的一帧
    assert len(usage_frames) == 1
    assert usage_frames[0]["usage"] == USAGE


def test_stream_without_reasoning_or_tool_calls() -> None:
    only_text = ReplyDraft(final_text="ok")
    frames = list(chat_protocol.stream_frames(MODEL, only_text))
    payloads = [json.loads(frame.data) for frame in frames[:-1]]
    assert not any("reasoning_content" in item["choices"][0]["delta"] for item in payloads)
    assert not any("tool_calls" in item["choices"][0]["delta"] for item in payloads)
    # finish_reason == "stop"
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_without_usage_flag_skips_usage_frame() -> None:
    frames = list(chat_protocol.stream_frames(MODEL, REPLY, usage=USAGE, include_usage=False))
    last_two = frames[-2:]
    assert last_two[0].data.startswith('{"id":'), "倒数第二帧必须是 choice 帧"
    assert last_two[1].data == "[DONE]"
    payloads = [json.loads(frame.data) for frame in frames[:-1]]
    assert all("usage" not in item for item in payloads if item["choices"])


@pytest.mark.parametrize("field", ["id", "object", "created", "model", "choices", "usage"])
def test_required_top_fields_present(field: str) -> None:
    body = chat_protocol.render_response(MODEL, REPLY, usage=USAGE)
    assert field in body

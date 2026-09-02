"""人工回复 → Anthropic Messages 契约快照。"""

from __future__ import annotations

import json

from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import anthropic as anthropic_protocol

USAGE = {"input_tokens": 30, "output_tokens": 12}

REPLY = ReplyDraft(
    reasoning="想一下天气",
    tool_calls=[ReplyToolCall(id="call_a1", name="get_weather", arguments={"city": "北京"})],
    final_text="北京今天晴，23°C。",
)
MODEL = "claude-haiku-5"


def test_render_shape() -> None:
    body = anthropic_protocol.render_response(MODEL, REPLY, usage=USAGE)
    assert body["id"].startswith("msg_")
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == MODEL
    # 人工不伪造 thinking block
    assert all(block["type"] != "thinking" for block in body["content"])
    types = [block["type"] for block in body["content"]]
    assert types == ["tool_use", "text"]
    tool_block = body["content"][0]
    assert tool_block["name"] == "get_weather"
    assert tool_block["input"] == {"city": "北京"}
    assert body["content"][-1] == {"type": "text", "text": REPLY.final_text}
    assert body["stop_reason"] == "tool_use"
    assert body["usage"] == USAGE


def test_render_text_only_end_turn() -> None:
    body = anthropic_protocol.render_response(MODEL, ReplyDraft(final_text="ok"), usage=USAGE)
    assert body["stop_reason"] == "end_turn"
    assert all(block["type"] == "text" for block in body["content"])


def test_stream_sequence_and_usage() -> None:
    events = list(anthropic_protocol.stream_events(MODEL, REPLY, usage=USAGE))
    names = [e.event for e in events]
    data = [json.loads(e.data) for e in events]

    assert names[0] == "message_start"
    assert names[-1] == "message_stop"

    # message_start 的 usage 只含 input_tokens，output_tokens 归 0
    start_msg = data[0]["message"]
    assert start_msg["usage"]["input_tokens"] == USAGE["input_tokens"]
    assert start_msg["usage"]["output_tokens"] == 0

    # 每个块：content_block_start -> content_block_delta* -> content_block_stop
    starts = [i for i, name in enumerate(names) if name == "content_block_start"]
    stops = [i for i, name in enumerate(names) if name == "content_block_stop"]
    assert len(starts) == 2 and len(stops) == 2
    assert starts[0] < stops[0] < starts[1] < stops[1]

    # tool_use 增量是 input_json_delta，text 是 text_delta
    first_delta_types = [
        item["delta"]["type"] for item in data if item.get("type") == "content_block_delta"
    ]
    assert "input_json_delta" in first_delta_types
    assert "text_delta" in first_delta_types

    # message_delta 携带 stop_reason + 输出 usage
    delta_frame = next(item for item in data if item.get("type") == "message_delta")
    assert delta_frame["delta"]["stop_reason"] == "tool_use"
    assert delta_frame["usage"]["output_tokens"] == USAGE["output_tokens"]

    # text delta 累计 = final_text
    text = "".join(
        item["delta"]["text"]
        for item in data
        if item.get("type") == "content_block_delta" and item["delta"]["type"] == "text_delta"
    )
    assert text == REPLY.final_text

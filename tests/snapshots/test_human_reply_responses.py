"""人工回复 → OpenAI Responses 契约快照。"""

from __future__ import annotations

import json

from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import responses as responses_protocol

USAGE = {"input_tokens": 31, "output_tokens": 11, "total_tokens": 42}

REPLY = ReplyDraft(
    reasoning="想一下天气",
    tool_calls=[ReplyToolCall(id="call_a1", name="get_weather", arguments={"city": "北京"})],
    final_text="北京今天晴，23°C。",
)
MODEL = "deepseek-v4-pro"


def test_render_shape() -> None:
    body = responses_protocol.render_response(MODEL, "resp_x", REPLY, usage=USAGE)
    assert body["id"] == "resp_x"
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["model"] == MODEL
    assert body["usage"] == USAGE
    # 输出 item 顺序：reasoning -> function_call -> message
    assert [item["type"] for item in body["output"]] == ["reasoning", "function_call", "message"]
    message = next(item for item in body["output"] if item["type"] == "message")
    assert message["content"][0]["type"] == "output_text"
    assert message["content"][0]["annotations"] == []
    assert message["content"][0]["text"] == REPLY.final_text
    assert message["id"]


def test_stream_event_sequence() -> None:
    events = list(responses_protocol.stream_events(MODEL, "resp_x", REPLY, usage=USAGE))
    names = [e.event for e in events]
    data = [json.loads(e.data) for e in events]

    # SDK 必需事件顺序骨架
    assert names[0] == "response.created"
    assert names[1] == "response.in_progress"
    assert names[-1] == "response.completed"

    # 每个事件带单调 sequence_number
    seq = [item["sequence_number"] for item in data]
    assert seq == sorted(seq)
    assert all(isinstance(v, int) for v in seq)

    # content part / delta / done 事件齐备
    for required in (
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    ):
        assert required in names, required

    # output_item.added 顺序与 render 一致
    added = [d for d in data if d["type"] == "response.output_item.added"]
    assert [a["item"]["type"] for a in added] == ["reasoning", "function_call", "message"]

    # completed 帧带完整 usage + 已完成的 response_body
    done = data[-1]
    assert done["response"]["status"] == "completed"
    assert done["response"]["usage"] == USAGE
    # output_text delta 累计 = final_text
    deltas = [d["delta"] for d in data if d["type"] == "response.output_text.delta"]
    assert "".join(deltas) == REPLY.final_text


def test_stream_no_tool_call_stride() -> None:
    """无 tool_call 的回复不产出 function_call 类事件。"""
    text_only = ReplyDraft(reasoning="r", final_text="ok")
    events = list(responses_protocol.stream_events(MODEL, "resp_y", text_only, usage=USAGE))
    names = [e.event for e in events]
    assert "response.function_call_arguments.delta" not in names
    assert "response.function_call_arguments.done" not in names

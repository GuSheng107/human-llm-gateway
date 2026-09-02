"""Token 快照跨协议一致性：同一请求对应三协议同一份 input/output。

TokenSnapshot.build 只算一次，输出与流式/非流式无关。
"""

from __future__ import annotations

from app.domain.tokens import TokenSnapshot
from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import (
    anthropic as anthropic_protocol,
)
from app.protocols import (
    chat_completions as chat_protocol,
)
from app.protocols import (
    responses as responses_protocol,
)

FULL_DRAFT = ReplyDraft(
    reasoning="想一下",
    tool_calls=[ReplyToolCall(id="c1", name="get", arguments={"x": 1})],
    final_text="OK",
)
FULL_CONTEXT = [
    {"role": "developer", "content": "系统"},
    {"role": "user", "content": "问题 1"},
    {"role": "assistant", "content": "回答 1"},
    {"role": "tool", "content": '{"x": 1}'},
    {"role": "user", "content": "问题 2"},
]
INSTRUCTIONS = "运行规则"


def _snap() -> TokenSnapshot:
    return TokenSnapshot.build(
        context=FULL_CONTEXT,
        instructions=INSTRUCTIONS,
        reasoning=FULL_DRAFT.reasoning,
        tool_calls=[call.model_dump() for call in FULL_DRAFT.tool_calls],
        final_text=FULL_DRAFT.final_text,
    )


def test_snapshot_single_call_consistent() -> None:
    snap = _snap()
    assert snap.input_tokens > 0
    assert snap.output_tokens > 0
    assert snap.total_tokens == snap.input_tokens + snap.output_tokens

    # 相同输入必须产出相同快照
    snap2 = _snap()
    assert snap2.input_tokens == snap.input_tokens
    assert snap2.output_tokens == snap.output_tokens


def test_snapshot_used_by_all_three_protocols() -> None:
    snap = _snap()
    chat_usage = {
        "prompt_tokens": snap.input_tokens,
        "completion_tokens": snap.output_tokens,
        "total_tokens": snap.total_tokens,
    }
    responses_usage = {
        "input_tokens": snap.input_tokens,
        "output_tokens": snap.output_tokens,
        "total_tokens": snap.total_tokens,
    }
    anthropic_usage = {"input_tokens": snap.input_tokens, "output_tokens": snap.output_tokens}

    chat_body = chat_protocol.render_response("deepseek-v4-pro", FULL_DRAFT, usage=chat_usage)
    assert chat_body["usage"] == chat_usage

    resp_body = responses_protocol.render_response(
        "deepseek-v4-pro", "resp_x", FULL_DRAFT, usage=responses_usage
    )
    assert resp_body["usage"] == responses_usage

    anthropic_body = anthropic_protocol.render_response(
        "claude-haiku-5", FULL_DRAFT, usage=anthropic_usage
    )
    assert anthropic_body["usage"] == anthropic_usage

    # 数值一致（不因协议改变）
    assert (
        chat_usage["prompt_tokens"]
        == responses_usage["input_tokens"]
        == anthropic_usage["input_tokens"]
    )
    assert (
        chat_usage["completion_tokens"]
        == responses_usage["output_tokens"]
        == anthropic_usage["output_tokens"]
    )


def test_snapshot_input_uses_full_context_not_only_latest() -> None:
    partial = TokenSnapshot.build(
        context=FULL_CONTEXT[4:],
        instructions=None,
        reasoning=None,
        tool_calls=None,
        final_text="",
    )
    full = _snap()
    assert full.input_tokens > partial.input_tokens

"""M7-D 跨协议字段矩阵与上游流式测试（docs/API_CONTRACT.md §12.6）。

矩阵逐项：
- 系统指令 / 用户助手内容 / developer role
- 输出上限（max_tokens <-> max_output_tokens）
- 采样参数 / 停止序列（stop 字符串 <-> 数组 <-> stop_sequences）
- 工具 Schema（function.parameters <-> input_schema）
- 工具选择（required <-> any；指定函数 <-> tool{name}）
- 并行工具（parallel_tool_calls <-> disable_parallel_tool_use 取反）
- metadata（user <-> metadata.user_id）
- 拒绝：reasoning 控制 / response_format / cache_control / service_tier /
  托管工具 / 未知字段

流式：
- Chat delta（content / reasoning_content / tool_calls 增量）
- Anthropic 事件（text_delta / thinking_delta / input_json_delta）
- collect/finalize 累积语义
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.errors import DomainError, DomainErrorCode
from app.protocols import cross
from app.services.llm_upstream import (
    UpstreamChunk,
    collect_chunk,
    finalize_collected,
)


def _norm(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "context": [{"role": "user", "content": "hi"}],
        "instructions": None,
        "system_blocks": None,
        "tools": None,
        "tool_choice": None,
        "options": {},
        "stream": False,
    }
    base.update(overrides)
    return base


def _unsupported(exc: pytest.ExceptionInfo) -> bool:
    return exc.value.code is DomainErrorCode.UNSUPPORTED_PARAMETER


# ----------------------------------------------------------------------
# 内容与系统指令
# ----------------------------------------------------------------------


def test_to_chat_converts_instructions_to_system_message() -> None:
    body = cross.to_chat_request(_norm(instructions="Be concise"), "gpt")
    assert body["messages"][0] == {"role": "system", "content": "Be concise"}


def test_to_chat_converts_anthropic_system_blocks() -> None:
    body = cross.to_chat_request(
        _norm(system_blocks=[{"type": "text", "text": "b1"}, {"type": "text", "text": "b2"}]),
        "gpt",
    )
    assert body["messages"][0] == {"role": "system", "content": "b1\nb2"}


def test_to_anthropic_converts_instructions_to_system() -> None:
    body = cross.to_anthropic_request(_norm(instructions="Be terse"), "claude")
    assert body["system"] == "Be terse"


def test_to_chat_developer_role_becomes_system() -> None:
    normalized = _norm(
        context=[{"role": "developer", "content": "dev rules"}],
    )
    body = cross.to_chat_request(normalized, "gpt")
    assert body["messages"][0] == {"role": "system", "content": "dev rules"}


def test_to_anthropic_rejects_system_role_in_context() -> None:
    normalized = _norm(context=[{"role": "system", "content": "x"}])
    with pytest.raises(DomainError) as exc:
        cross.to_anthropic_request(normalized, "claude")
    assert _unsupported(exc)


# ----------------------------------------------------------------------
# 输出上限 / 采样 / 停止
# ----------------------------------------------------------------------


def test_output_limit_from_max_completion_tokens() -> None:
    normalized = _norm(options={"max_completion_tokens": 512})
    body = cross.to_chat_request(normalized, "gpt")
    assert body["max_tokens"] == 512


def test_output_limit_from_max_output_tokens_to_anthropic() -> None:
    normalized = _norm(options={"max_output_tokens": 256})
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["max_tokens"] == 256


def test_output_limit_conflict_rejected() -> None:
    """§12.6：同请求多个输出上限键 -> 400，即使数值相同也不静默择一。"""
    normalized = _norm(
        options={"max_completion_tokens": 512, "max_tokens": 256},
    )
    with pytest.raises(DomainError) as exc:
        cross.to_chat_request(normalized, "gpt")
    assert _unsupported(exc)

    # 数值相同同样拒绝：调用方无法确认语义一致性。
    with pytest.raises(DomainError):
        cross.to_chat_request(
            _norm(options={"max_completion_tokens": 512, "max_tokens": 512}), "gpt"
        )

    # 单键存在时正常转换。
    body = cross.to_chat_request(_norm(options={"max_tokens": 256}), "gpt")
    assert body["max_tokens"] == 256


def test_sample_params_convert() -> None:
    normalized = _norm(options={"temperature": 0.5, "top_p": 0.9})
    chat = cross.to_chat_request(normalized, "gpt")
    assert chat["temperature"] == 0.5
    assert chat["top_p"] == 0.9
    anthropic = cross.to_anthropic_request(normalized, "claude")
    assert anthropic["temperature"] == 0.5
    assert anthropic["top_p"] == 0.9


def test_to_responses_preserves_stateless_store_false() -> None:
    body = cross.to_responses_request(_norm(store=False), "gpt")
    assert body["store"] is False


def test_stop_string_to_anthropic_becomes_array() -> None:
    normalized = _norm(options={"stop": "END"})
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["stop_sequences"] == ["END"]


def test_stop_sequences_to_chat_passthrough() -> None:
    normalized = _norm(options={"stop_sequences": ["A", "B"]})
    body = cross.to_chat_request(normalized, "gpt")
    assert body["stop"] == ["A", "B"]


# ----------------------------------------------------------------------
# 工具 Schema / 选择 / 并行
# ----------------------------------------------------------------------


def test_responses_function_tool_to_chat_shape() -> None:
    normalized = _norm(
        tools=[{"type": "function", "name": "search", "parameters": {"type": "object"}}],
    )
    body = cross.to_chat_request(normalized, "gpt")
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": None,
                "parameters": {"type": "object"},
            },
        }
    ]


def test_chat_tool_to_anthropic_input_schema() -> None:
    normalized = _norm(
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "查找",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["tools"] == [
        {
            "name": "search",
            "description": "查找",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_managed_tool_type_rejected() -> None:
    normalized = _norm(tools=[{"type": "file_search"}])
    with pytest.raises(DomainError) as exc:
        cross.to_chat_request(normalized, "gpt")
    assert _unsupported(exc)


def test_tool_choice_required_to_any() -> None:
    body = cross.to_anthropic_request(_norm(tool_choice="required"), "claude")
    assert body["tool_choice"] == {"type": "any"}


def test_tool_choice_named_function_to_anthropic_tool() -> None:
    normalized = _norm(
        tool_choice={"type": "function", "function": {"name": "search"}},
    )
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["tool_choice"] == {"type": "tool", "name": "search"}


def test_tool_choice_anthropic_tool_to_chat_function() -> None:
    normalized = _norm(tool_choice={"type": "tool", "name": "search"})
    body = cross.to_chat_request(normalized, "gpt")
    assert body["tool_choice"] == {"type": "function", "function": {"name": "search"}}


def test_parallel_tool_calls_false_to_disable_flag() -> None:
    normalized = _norm(options={"parallel_tool_calls": False})
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_parallel_tool_calls_true_no_flag() -> None:
    normalized = _norm(options={"parallel_tool_calls": True})
    body = cross.to_anthropic_request(normalized, "claude")
    assert "disable_parallel_tool_use" not in str(body)


def test_chat_tool_role_to_anthropic_tool_result() -> None:
    normalized = _norm(
        context=[
            {"role": "tool", "tool_call_id": "c1", "content": "result text"},
        ],
    )
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"][0]["type"] == "tool_result"
    assert body["messages"][0]["content"][0]["tool_use_id"] == "c1"


def test_chat_assistant_tool_calls_to_anthropic_tool_use() -> None:
    normalized = _norm(
        context=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q": "x"}'},
                    }
                ],
            },
        ],
    )
    body = cross.to_anthropic_request(normalized, "claude")
    blocks = body["messages"][0]["content"]
    tool_use = next(b for b in blocks if b["type"] == "tool_use")
    assert tool_use["id"] == "c1"
    assert tool_use["name"] == "search"
    assert tool_use["input"] == {"q": "x"}


# ----------------------------------------------------------------------
# metadata
# ----------------------------------------------------------------------


def test_metadata_user_to_chat_user() -> None:
    normalized = _norm(options={"metadata": {"user_id": "u1"}})
    body = cross.to_chat_request(normalized, "gpt")
    assert body["user"] == "u1"


def test_user_to_anthropic_metadata() -> None:
    normalized = _norm(options={"user": "u1"})
    body = cross.to_anthropic_request(normalized, "claude")
    assert body["metadata"] == {"user_id": "u1"}


def test_metadata_extra_keys_rejected() -> None:
    normalized = _norm(options={"metadata": {"user_id": "u1", "trace": "t1"}})
    with pytest.raises(DomainError) as exc:
        cross.to_anthropic_request(normalized, "claude")
    assert _unsupported(exc)


# ----------------------------------------------------------------------
# 拒绝矩阵
# ----------------------------------------------------------------------


def test_response_format_to_anthropic_rejected() -> None:
    normalized = _norm(options={"response_format": {"type": "json_object"}})
    with pytest.raises(DomainError) as exc:
        cross.to_anthropic_request(normalized, "claude")
    assert "response_format" in exc.value.message


def test_reasoning_control_cross_protocol_rejected() -> None:
    normalized = _norm(options={"reasoning": {"effort": "high"}})
    with pytest.raises(DomainError) as exc:
        cross.to_anthropic_request(normalized, "claude")
    assert _unsupported(exc)


def test_thinking_to_chat_rejected() -> None:
    normalized = _norm(options={"thinking": {"type": "enabled", "budget_tokens": 1024}})
    with pytest.raises(DomainError) as exc:
        cross.to_chat_request(normalized, "gpt")
    assert _unsupported(exc)


def test_service_tier_cross_protocol_rejected() -> None:
    normalized = _norm(options={"service_tier": "priority"})
    with pytest.raises(DomainError) as exc:
        cross.to_anthropic_request(normalized, "claude")
    assert _unsupported(exc)


def test_cache_control_in_content_rejected() -> None:
    normalized = _norm(
        context=[
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
            }
        ],
    )
    with pytest.raises(DomainError) as exc:
        cross.to_chat_request(normalized, "gpt")
    assert _unsupported(exc)


def test_unknown_field_rejected_not_ignored() -> None:
    normalized = _norm(options={"vendor_custom_field": 1})
    with pytest.raises(DomainError) as exc:
        cross.to_chat_request(normalized, "gpt")
    assert "vendor_custom_field" in exc.value.message


# ----------------------------------------------------------------------
# 流式增量（UpstreamChunk / collect / finalize）
# ----------------------------------------------------------------------


def test_collect_text_and_reasoning() -> None:
    target: dict[str, Any] = {}
    collect_chunk(target, UpstreamChunk(text="Hel"))
    collect_chunk(target, UpstreamChunk(text="lo"))
    collect_chunk(target, UpstreamChunk(reasoning="think"))
    result = finalize_collected(target)
    assert result["final_text"] == "Hello"
    assert result["reasoning"] == "think"


def test_collect_chat_tool_call_deltas() -> None:
    target: dict[str, Any] = {}
    collect_chunk(
        target,
        UpstreamChunk(tool_call={"id": "c1", "name": "search", "arguments_delta": '{"q"'}),
    )
    collect_chunk(target, UpstreamChunk(tool_call={"arguments_delta": ': "x"}'}))
    result = finalize_collected(target)
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "c1"
    assert result["tool_calls"][0]["name"] == "search"
    assert result["tool_calls"][0]["arguments"] == {"q": "x"}


def test_collect_parallel_tool_calls_by_index() -> None:
    """并行多 tool_call：arguments 增量按 index 归位，不串位到末调用。"""
    target: dict[str, Any] = {}
    # index 0 开始：c1/search
    collect_chunk(
        target,
        UpstreamChunk(
            tool_call={"index": 0, "id": "c1", "name": "search", "arguments_delta": '{"a"'}
        ),
    )
    # index 1 开始：c2/calc（并行插入）
    collect_chunk(
        target,
        UpstreamChunk(
            tool_call={"index": 1, "id": "c2", "name": "calc", "arguments_delta": '{"b"'}
        ),
    )
    # 交错增量：先 0 后 1 再 0
    collect_chunk(target, UpstreamChunk(tool_call={"index": 0, "arguments_delta": ": 1}"}))
    collect_chunk(target, UpstreamChunk(tool_call={"index": 1, "arguments_delta": ": 2}"}))
    result = finalize_collected(target)
    assert len(result["tool_calls"]) == 2
    first, second = result["tool_calls"]
    assert first["id"] == "c1"
    assert first["name"] == "search"
    assert first["arguments"] == {"a": 1}
    assert second["id"] == "c2"
    assert second["name"] == "calc"
    assert second["arguments"] == {"b": 2}


def test_parse_chat_delta_content_and_reasoning() -> None:
    from app.services.llm_upstream import _parse_chat_delta

    chunk = _parse_chat_delta(
        {"choices": [{"delta": {"content": "hi", "reasoning_content": "why"}}]}
    )
    assert chunk.text == "hi"
    assert chunk.reasoning == "why"


def test_parse_chat_delta_tool_call_start() -> None:
    from app.services.llm_upstream import _parse_chat_delta

    chunk = _parse_chat_delta(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "search", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert chunk.tool_call is not None
    assert chunk.tool_call["id"] == "c1"
    assert chunk.tool_call["name"] == "search"


def test_parse_anthropic_text_delta_event() -> None:
    from app.services.llm_upstream import _parse_anthropic_event

    chunk = _parse_anthropic_event(
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {},
    )
    assert chunk is not None and chunk.text == "hi"


def test_parse_anthropic_tool_use_lifecycle() -> None:
    from app.services.llm_upstream import _parse_anthropic_event

    buffers: dict[int, dict[str, str]] = {}
    start = _parse_anthropic_event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "t1", "name": "lookup"},
        },
        buffers,
    )
    assert start is None
    assert 1 in buffers
    mid = _parse_anthropic_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"k"'},
        },
        buffers,
    )
    assert mid is None
    stop = _parse_anthropic_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": ': "v"}'},
        },
        buffers,
    )
    assert stop is None
    done = _parse_anthropic_event(
        "content_block_stop", {"type": "content_block_stop", "index": 1}, buffers
    )
    assert done is not None and done.tool_call is not None
    assert done.tool_call["id"] == "t1"
    assert done.tool_call["name"] == "lookup"
    assert done.tool_call["arguments"] == {"k": "v"}


def test_finalize_empty_target() -> None:
    result = finalize_collected({})
    assert result == {"reasoning": None, "tool_calls": [], "final_text": None}

"""Anthropic 上游流式历史：工具参数分片损坏时必须按协议报上游错误。

该 input 会在续轮时原样回传给上游（`append_tool_results` 的 Anthropic 分支
直接 deepcopy `native_response["content"]`），所以既不能冒泡成 500，也不能被
"修复"成空对象或非对象。
"""

import pytest

from app.domain.errors import DomainError, DomainErrorCode
from app.protocols.assistant import AnthropicStreamHistory


def _start_tool_use(history: AnthropicStreamHistory) -> None:
    history.consume(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t1", "name": "lookup"},
        },
    )


def _feed_fragment(history: AnthropicStreamHistory, fragment: str) -> None:
    history.consume(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": fragment},
        },
    )


def _stop(history: AnthropicStreamHistory) -> None:
    history.consume("content_block_stop", {"type": "content_block_stop", "index": 0})


def test_streamed_arguments_are_assembled_into_object() -> None:
    """跨多个分片的参数拼接后落成 input 对象，并保留 thinking 签名。"""
    history = AnthropicStreamHistory()
    _start_tool_use(history)
    _feed_fragment(history, '{"k"')
    _feed_fragment(history, ': "v"}')
    _stop(history)

    assert history.response()["content"] == [
        {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"k": "v"}}
    ]


def test_broken_partial_json_raises_upstream_error() -> None:
    """损坏分片按协议转成上游错误，不冒泡成 500。"""
    history = AnthropicStreamHistory()
    _start_tool_use(history)
    _feed_fragment(history, '{"k": ')

    with pytest.raises(DomainError) as excinfo:
        _stop(history)

    assert excinfo.value.code is DomainErrorCode.UPSTREAM_ERROR
    assert excinfo.value.status_code == 502


def test_non_object_arguments_are_rejected() -> None:
    """合法 JSON 但不是对象时同样拒绝：续轮不能回传非对象 input。"""
    history = AnthropicStreamHistory()
    _start_tool_use(history)
    _feed_fragment(history, "[1, 2]")

    with pytest.raises(DomainError) as excinfo:
        _stop(history)

    assert excinfo.value.code is DomainErrorCode.UPSTREAM_ERROR
    assert excinfo.value.status_code == 502


def test_text_blocks_are_not_treated_as_tool_arguments() -> None:
    """纯文本块不接受 input_json_delta，事件流不会被参数校验误伤。"""
    history = AnthropicStreamHistory()
    history.consume(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    )
    history.consume(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "你好"},
        },
    )
    _stop(history)

    assert history.response()["content"] == [{"type": "text", "text": "你好"}]

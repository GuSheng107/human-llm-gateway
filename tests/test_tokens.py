"""确定性本地 Token 估算器测试（方案4：统一估算，三协议共享快照）。"""

from __future__ import annotations

from app.domain.tokens import (
    TokenSnapshot,
    estimate_context_tokens,
    estimate_json_tokens,
    estimate_output_tokens,
    estimate_text_tokens,
)


def test_estimate_text_tokens_cjk_vs_ascii() -> None:
    # CJK 按字符计；ASCII 按 4 字符/token。
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("你好") == 2
    assert estimate_text_tokens("abcdefgh") == 2
    # 混合：4 个 CJK + 8 个 ASCII。
    assert estimate_text_tokens("你好世界abcdefgh") == 4 + 2


def test_estimate_json_tokens_includes_structure_overhead() -> None:
    value = {"city": "北京"}
    tokens = estimate_json_tokens(value)
    assert tokens >= estimate_text_tokens('{"city": "北京"}')


def test_estimate_context_tokens_covers_all_message_kinds() -> None:
    context = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": [{"type": "text", "text": "查天气"}]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "北京"}}}],
        },
        {"role": "tool", "name": "get_weather", "content": "晴"},
    ]
    tokens = estimate_context_tokens(context, instructions="全局指令")
    assert tokens > 0


def test_estimate_output_tokens_reasoning_tools_text() -> None:
    tokens = estimate_output_tokens(
        reasoning="思考",
        tool_calls=[{"name": "get_weather", "arguments": {"city": "北京"}, "id": "call_1"}],
        final_text="晴",
    )
    assert tokens > 0
    # 空输出保底 1。
    assert estimate_output_tokens() == 1


def test_token_snapshot_build_and_total() -> None:
    snapshot = TokenSnapshot.build(
        context=[{"role": "user", "content": "你好"}],
        instructions=None,
        reasoning="想",
        tool_calls=[],
        final_text="答",
    )
    assert snapshot.input_tokens >= 1
    assert snapshot.output_tokens >= 1
    assert snapshot.total_tokens == snapshot.input_tokens + snapshot.output_tokens

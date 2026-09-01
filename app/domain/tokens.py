"""确定性本地 Token 估算器。

Fake Model 没有真实 tokenizer，无法保证供应商级精确值；这里提供统一、
确定性的估算：完整计算输入消息、推理、正文和 tool JSON，三种协议输出
同一份快照。估算规则：

- 中文等 CJK 字符约 1 token/字符；
- 其他文本约 1 token/4 字符（保留空格与数字的简单切分修正）；
- JSON 结构（tool call arguments 等）按序列化后的长度估算并附加结构开销。

同一份 (input_tokens, output_tokens) 在 Chat / Responses / Anthropic 渲染
与流式/非流式之间共享，保证同一次响应内数值稳定。
"""

from __future__ import annotations

import json
import re
from typing import Any

_CJK_RANGE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\ufb00-\ufb4f]")

# JSON 序列化结构开销（键名、引号、括号等）的固定附加。
_JSON_OVERHEAD_PER_NODE = 2


def estimate_text_tokens(text: str) -> int:
    """确定性文本 token 估算：CJK 按字符计，其余按 4 字符/token。"""
    if not text:
        return 0
    cjk_count = len(_CJK_RANGE.findall(text))
    non_cjk = len(text) - cjk_count
    tokens = cjk_count + (non_cjk + 3) // 4
    return max(1, tokens)


def estimate_json_tokens(value: Any) -> int:
    """JSON 值 token 估算：按序列化长度 + 结构节点开销。"""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    node_count = serialized.count("{") + serialized.count("[")
    return estimate_text_tokens(serialized) + node_count * _JSON_OVERHEAD_PER_NODE


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                inner = block.get("text") or block.get("content") or block.get("output") or ""
                if isinstance(inner, str):
                    parts.append(inner)
                elif inner is not None:
                    parts.append(json.dumps(inner, ensure_ascii=False, default=str))
                # tool_use / tool_result 块的 name/input 也计入输入。
                for key in ("input", "arguments", "result"):
                    if key in block:
                        parts.append(json.dumps(block[key], ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


def estimate_context_tokens(context: list[Any], *, instructions: str | None = None) -> int:
    """输入侧 token：完整遍历全部消息（含 system/developer/user/assistant/tool）。"""
    total = estimate_text_tokens(instructions or "")
    for item in context:
        if isinstance(item, dict):
            role = str(item.get("role") or "")
            total += estimate_text_tokens(role)
            total += estimate_text_tokens(_content_to_text(item.get("content")))
            # tool call 字段
            if "tool_calls" in item and isinstance(item.get("tool_calls"), list):
                for call in item["tool_calls"]:
                    if isinstance(call, dict):
                        function = call.get("function") or {}
                        total += estimate_text_tokens(str(function.get("name") or ""))
                        total += estimate_json_tokens(function.get("arguments"))
            if "name" in item:
                total += estimate_text_tokens(str(item.get("name") or ""))
        else:
            total += estimate_json_tokens(item)
    return max(1, total)


def estimate_output_tokens(
    *,
    reasoning: str | None = None,
    tool_calls: list[Any] | None = None,
    final_text: str | None = None,
) -> int:
    """输出侧 token：推理 + tool call JSON + 正文。"""
    total = estimate_text_tokens(reasoning or "")
    for call in tool_calls or []:
        if isinstance(call, dict):
            total += estimate_text_tokens(str(call.get("name") or ""))
            total += estimate_json_tokens(call.get("arguments"))
            total += estimate_text_tokens(str(call.get("id") or ""))
        else:
            name = getattr(call, "name", "")
            arguments = getattr(call, "arguments", None)
            call_id = getattr(call, "id", "")
            total += estimate_text_tokens(str(name or ""))
            total += estimate_json_tokens(arguments)
            total += estimate_text_tokens(str(call_id or ""))
    total += estimate_text_tokens(final_text or "")
    return max(1, total)


class TokenSnapshot:
    """同一次回复的 token 快照：流式与非流式共享同一份数值。"""

    __slots__ = ("input_tokens", "output_tokens")

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @classmethod
    def build(
        cls,
        *,
        context: list[Any] | None = None,
        instructions: str | None = None,
        reasoning: str | None = None,
        tool_calls: list[Any] | None = None,
        final_text: str | None = None,
    ) -> TokenSnapshot:
        input_tokens = estimate_context_tokens(context or [], instructions=instructions)
        output_tokens = estimate_output_tokens(
            reasoning=reasoning, tool_calls=tool_calls, final_text=final_text
        )
        return cls(input_tokens, output_tokens)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

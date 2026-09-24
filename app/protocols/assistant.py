"""小助手的原生协议请求与只读工具续轮；不读取任务或执行工具。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from ..domain.enums import LLMProtocol
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from .upstream_reply import (
    parse_anthropic_response,
    parse_chat_response,
    parse_responses_response,
)


def build_request(
    protocol: LLMProtocol,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """把内部文本历史和 MCP definitions 映射到目标原生协议。"""
    system = messages[0]["content"]
    history = deepcopy(messages[1:])
    functions = [
        {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}
        for t in tools
    ]
    if protocol is LLMProtocol.OPENAI_CHAT:
        body = {"model": model, "messages": deepcopy(messages)}
        native_tools = [{"type": "function", "function": f} for f in functions]
    elif protocol is LLMProtocol.OPENAI_RESPONSES:
        body = {"model": model, "instructions": system, "input": history, "store": False}
        native_tools = [{"type": "function", **f, "strict": False} for f in functions]
    else:
        body = {"model": model, "system": system, "messages": history}
        native_tools = [
            {"name": f["name"], "description": f["description"], "input_schema": f["parameters"]}
            for f in functions
        ]
    # 显式空列表防止配置 extra_body 注入非注册工具。
    body["tools"] = native_tools
    return body


def ensure_anthropic_budget(body: dict[str, Any], *, default_output_tokens: int = 2048) -> None:
    """显式上限不被覆盖；未指定时为 thinking 留出预算和正文空间。"""
    thinking = body.get("thinking") or {}
    budget = thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0
    body.setdefault("max_tokens", budget + default_output_tokens)
    if budget and body["max_tokens"] <= budget:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "输出上限必须大于 thinking 预算", status_code=400
        )
    choice = body.get("tool_choice") or {}
    if budget and isinstance(choice, dict) and choice.get("type") in ("any", "tool"):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "thinking 不支持强制工具选择", status_code=400
        )


def parse_reply(protocol: LLMProtocol, upstream: dict[str, Any]) -> ReplyDraft:
    """复用三协议严格参数解析；损坏参数不得执行为 {}。"""
    parsers = {
        LLMProtocol.OPENAI_CHAT: parse_chat_response,
        LLMProtocol.OPENAI_RESPONSES: parse_responses_response,
        LLMProtocol.ANTHROPIC_MESSAGES: parse_anthropic_response,
    }
    return parsers[protocol](upstream)


def validate_round(reply: ReplyDraft) -> None:
    """在任何工具执行前校验整轮 ID 和调用数量。"""
    ids = [call.id for call in reply.tool_calls]
    if len(ids) > 20 or len(ids) != len(set(ids)) or any(not call_id.strip() for call_id in ids):
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游工具调用无效", status_code=502)


def append_tool_results(
    protocol: LLMProtocol,
    body: dict[str, Any],
    reply: ReplyDraft,
    results: list[dict[str, Any]],
    *,
    native_response: dict[str, Any] | None = None,
) -> None:
    """保留调用 ID、错误标志以及原生 reasoning/thinking 块，追加配对结果。"""
    calls = reply.tool_calls
    texts = [json.dumps(result, ensure_ascii=False) for result in results]
    if protocol is LLMProtocol.OPENAI_CHAT:
        message = {
            "role": "assistant",
            "content": reply.final_text or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in calls
            ],
        }
        if reply.reasoning:
            message["reasoning_content"] = reply.reasoning
        body["messages"].append(message)
        body["messages"].extend(
            {"role": "tool", "tool_call_id": call.id, "content": text}
            for call, text in zip(calls, texts, strict=True)
        )
    elif protocol is LLMProtocol.OPENAI_RESPONSES:
        output = (native_response or {}).get("output")
        if not isinstance(output, list):
            output = [
                {
                    "type": "function_call",
                    "call_id": c.id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in calls
            ]
        body["input"].extend(deepcopy(output))
        body["input"].extend(
            {"type": "function_call_output", "call_id": call.id, "output": text}
            for call, text in zip(calls, texts, strict=True)
        )
    else:
        content = (native_response or {}).get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": reply.final_text}] if reply.final_text else []
            content.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in calls
            )
        body["messages"].append({"role": "assistant", "content": deepcopy(content)})
        body["messages"].append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": text,
                        "is_error": bool(result.get("isError")),
                    }
                    for call, text, result in zip(calls, texts, results, strict=True)
                ],
            }
        )


def _parse_streamed_arguments(raw: str) -> dict[str, Any]:
    """上游分片拼出的工具参数必须是一次完整的 JSON 对象。

    损坏的分片不能冒泡成 500，也不允许被"修复"成空对象：该 input 在续轮时会
    原样回传给上游（见 append_tool_results 的 Anthropic 分支），必须是合法对象。
    """
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR, "上游工具参数不是合法 JSON", status_code=502
        ) from exc
    if not isinstance(parsed, dict):
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR, "上游工具参数不是 JSON 对象", status_code=502
        )
    return parsed


class AnthropicStreamHistory:
    """保留续轮所需的 thinking 签名；内容仅在当前请求内存中使用。"""

    def __init__(self) -> None:
        self.blocks: dict[int, dict[str, Any]] = {}
        self.arguments: dict[int, str] = {}
        self.usage: dict[str, Any] = {}

    def consume(self, event: str, payload: dict[str, Any]) -> None:
        kind = payload.get("type") or event
        index = payload.get("index", 0)
        if kind == "message_start":
            self.usage.update((payload.get("message") or {}).get("usage") or {})
        elif kind == "message_delta":
            self.usage.update(payload.get("usage") or {})
        elif kind == "content_block_start":
            self.blocks[index] = deepcopy(payload.get("content_block") or {})
        elif kind == "content_block_delta":
            block = self.blocks.setdefault(index, {})
            delta = payload.get("delta") or {}
            fields = {
                "text_delta": "text",
                "thinking_delta": "thinking",
                "signature_delta": "signature",
            }
            field = fields.get(delta.get("type"))
            if field:
                block[field] = block.get(field, "") + delta.get(field, "")
            elif delta.get("type") == "input_json_delta":
                self.arguments[index] = self.arguments.get(index, "") + delta.get(
                    "partial_json", ""
                )
        elif kind == "content_block_stop" and index in self.arguments:
            self.blocks[index]["input"] = _parse_streamed_arguments(self.arguments.pop(index))

    def response(self) -> dict[str, Any]:
        return {"content": [self.blocks[i] for i in sorted(self.blocks)], "usage": self.usage}

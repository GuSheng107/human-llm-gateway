"""IM 回复 DSL：把纯文本消息解析为 ReplyDraft，并可反向序列化（M6-B）。

IM DSL 解析结果与 Web 编辑器必须生成同一个 ReplyDraft，且往返不丢字段
（docs/API_CONTRACT.md §9、docs/PRODUCT.md §6.4）。

语法（作用在已剥离 `#<task_public_id>` 定位前缀后的正文上）：

- 围栏块以 `::: <type-spec>` 开头，以 `:::` 结尾，独占一行。
- `::: reasoning`           — 围栏内为思考内容。
- `::: tool <id> <name>`    — 围栏内为 JSON arguments 对象。
- 围栏外的非空行拼接为 final_text。
- 不含任何围栏块时，整段正文即 final_text（向后兼容 M4 纯文本回复）。
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from .errors import DomainError, DomainErrorCode
from .values import ReplyDraft, ReplyToolCall

_FENCE = ":::"


def extract_task_target(text: str) -> tuple[str | None, str]:
    """剥离可选的 `#<public_id> ` 前缀，返回 (public_id, body)。

    保持与 ConnectionService._submit_task_reply 一致的定位语义：
    `#<public_id>` 后若存在空格则剩余部分为正文，否则 body 为空。
    """
    stripped = text.strip()
    if not stripped.startswith("#"):
        return None, stripped
    public_id, _, rest = stripped[1:].partition(" ")
    return public_id.strip() or None, rest.strip()


def parse_reply(body: str) -> ReplyDraft:
    """把正文解析为 ReplyDraft；无围栏块时整段作为 final_text。"""
    reasoning: str | None = None
    tool_calls: list[ReplyToolCall] = []
    free_lines: list[str] = []

    lines = body.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped == _FENCE or not stripped.startswith(_FENCE):
            if stripped or free_lines:
                free_lines.append(line)
            i += 1
            continue
        spec = stripped[len(_FENCE) :].strip()
        i += 1
        content: list[str] = []
        while i < n and lines[i].strip() != _FENCE:
            content.append(lines[i])
            i += 1
        if i < n:
            i += 1
        block_text = "\n".join(content).strip()
        consumed = _consume_block(spec, block_text)
        if isinstance(consumed, str):
            reasoning = consumed
        else:
            tool_calls.append(consumed)
    final_text = "\n".join(free_lines).strip() or None
    return ReplyDraft(reasoning=reasoning, tool_calls=tool_calls, final_text=final_text)


def _consume_block(spec: str, content: str) -> str | ReplyToolCall:
    if spec == "reasoning":
        return content
    head, _, _ = spec.partition(" ")
    if head == "tool":
        parts = shlex.split(spec, posix=True)
        if len(parts) < 3 or parts[0] != "tool":
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "tool 围栏格式应为 ::: tool <id> <name>",
                status_code=400,
            )
        call_id = parts[1]
        name = parts[2]
        arguments = _parse_arguments(content, call_id)
        return ReplyToolCall(id=call_id, name=name, arguments=arguments)
    raise DomainError(
        DomainErrorCode.VALIDATION_FAILED,
        f"未知的围栏类型: {spec}",
        status_code=400,
    )


def _parse_arguments(content: str, call_id: str) -> dict[str, Any]:
    if not content:
        return {}
    try:
        value = json.loads(content)
    except ValueError as exc:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"tool {call_id} 的 arguments 必须是合法 JSON 对象",
            status_code=400,
        ) from exc
    if not isinstance(value, dict):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"tool {call_id} 的 arguments 必须是 JSON 对象",
            status_code=400,
        )
    return value


def serialize_reply(draft: ReplyDraft) -> str:
    """把 ReplyDraft 序列化为 DSL 正文（往返无损）。

    空草稿序列化为空串；纯 final_text 序列化为原文本（无围栏）。
    """
    parts: list[str] = []
    if draft.reasoning:
        parts.append(f"{_FENCE} reasoning\n{draft.reasoning}\n{_FENCE}")
    for call in draft.tool_calls:
        arguments = json.dumps(call.arguments, ensure_ascii=False)
        parts.append(f"{_FENCE} tool {call.id} {call.name}\n{arguments}\n{_FENCE}")
    if draft.final_text:
        parts.append(draft.final_text)
    return "\n\n".join(parts)


def parse_message(text: str) -> tuple[str | None, ReplyDraft]:
    """一步解析完整 IM 消息：先剥离任务定位，再解析正文为 ReplyDraft。"""
    public_id, body = extract_task_target(text)
    return public_id, parse_reply(body)


def is_empty_draft(draft: ReplyDraft) -> bool:
    return not (
        draft.reasoning or draft.tool_calls or (draft.final_text and draft.final_text.strip())
    )

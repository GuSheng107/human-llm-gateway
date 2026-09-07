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
import re
import shlex
from dataclasses import dataclass
from typing import Any

from .errors import DomainError, DomainErrorCode
from .values import ReplyDraft, ReplyToolCall

_FENCE = ":::"

# ---------------------------------------------------------------------------
# 斜杠命令层
# ---------------------------------------------------------------------------

_COMMAND_NAMES = ("ans", "res", "page", "file", "commit")
# `/cmd` 或 `/cmd <args...>`；args 按空白切分，正文取首个换行后的全部内容。
_COMMAND_RE = re.compile(r"^/(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<rest>.*)$", re.DOTALL)


@dataclass(frozen=True)
class Command:
    """IM 消息中的斜杠命令（/ans /res /page /file /commit）。

    - name：命令名（不含斜杠）。
    - args：命令行参数（如 /page 2 的 "2"、/file md 的 "md"）。
    - body：命令后的正文（/ans /res 的回复内容；/page /file /commit 为空）。
    - unknown：True 表示 `/` 开头但不是已知命令（调用方应拒绝而非当正文）。
    """

    name: str
    args: str = ""
    body: str = ""
    unknown: bool = False


def parse_command(text: str) -> Command | None:
    """识别消息开头的斜杠命令；非 `/` 开头返回 None（纯文本/围栏 DSL）。

    规则：
    - `/ans` `/res` 后剩余内容为正文（保留原始换行，围栏语法可用）。
    - `/page` `/file` 的参数取首行按空白切分的第一个 token，其余忽略。
    - `/commit` 无参数无正文，用于确认暂存草稿。
    - 其余 `/xxx` 标记为 unknown，避免把命令误当回复正文。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    match = _COMMAND_RE.match(stripped)
    if match is None:
        return Command(name="", unknown=True)
    name = match.group("name").lower()
    rest = match.group("rest")
    if name not in _COMMAND_NAMES:
        return Command(name=name, unknown=True)
    if name in ("ans", "res"):
        # 正文为命令词后的剩余内容；剥掉紧跟的一个分隔空白。
        body = rest[1:] if rest.startswith((" ", "\t")) else rest.lstrip("\n")
        return Command(name=name, body=body.strip())
    # page / file：参数取首行首个 token。
    first_line, _, _ = rest.partition("\n")
    tokens = first_line.split()
    args = tokens[0] if tokens else ""
    return Command(name=name, args=args)


def is_command_text(text: str) -> bool:
    """是否为斜杠命令消息（含未知命令），供快速分流判断。"""
    return parse_command(text) is not None


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

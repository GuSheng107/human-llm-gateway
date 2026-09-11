"""IM 回复指令：把纯文本消息解析为 ReplyDraft（M6-B）。

IM 回复不再使用围栏 DSL（`::: reasoning` / `::: tool`），纯文本消息直接作为
ReplyDraft 的 final_text。斜杠命令（/ans /res /page /file /commit）与
`#<task_public_id>` 任务定位仍由本模块解析（docs/API_CONTRACT.md §9、
docs/PRODUCT.md §6.4）。

任务定位两种等价语法：

- 命令在前（推荐）：`/ans #<task_public_id> <正文>`；
- 任务在前（兼容）：`#<task_public_id> /ans <正文>`。

命令词后的 `#<id>` 均提取为 Command.target；其余正文整段作为
final_text（向后兼容 M4 纯文本回复）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .values import ReplyDraft

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
    - target：命令后 `#<task_public_id>` 提取的任务定位（/ans #id 正文）。
    - unknown：True 表示 `/` 开头但不是已知命令（调用方应拒绝而非当正文）。
    """

    name: str
    args: str = ""
    body: str = ""
    target: str | None = None
    unknown: bool = False


def _split_target(text: str) -> tuple[str | None, str]:
    """提取前导 `#<task_public_id> ` 定位，返回 (target, 剩余正文)。

    与 extract_task_target 同语义：`#` 后到首个空格为 id；id 为空
    （如 `# 标题` 类 Markdown 正文）不视为定位，原文返回。
    """
    stripped = text.strip()
    if not stripped.startswith("#"):
        return None, text
    candidate, _, rest = stripped[1:].partition(" ")
    candidate = candidate.strip()
    if not candidate:
        return None, text
    return candidate, rest.strip()


def parse_command(text: str) -> Command | None:
    """识别消息开头的斜杠命令；非 `/` 开头返回 None（纯文本回复）。

    规则：
    - `/ans` `/res` 后剩余内容为正文（保留原始换行），可带 `#<id>` 定位。
    - `/page` `/file` 的参数取首行按空白切分的 token，首 token 可为 `#<id>`。
    - `/commit` 无参数无正文，用于确认暂存草稿，可带 `#<id>` 定位。
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
        target, body = _split_target(body)
        return Command(name=name, body=body, target=target)
    # page / file：参数取首行首个 token；首 token 为 #<id> 时作为任务定位。
    first_line, _, _ = rest.partition("\n")
    tokens = first_line.split()
    target = None
    if tokens and tokens[0].startswith("#") and len(tokens[0]) > 1:
        target = tokens[0][1:]
        tokens = tokens[1:]
    args = tokens[0] if tokens else ""
    if name == "commit":
        return Command(name=name, target=target)
    return Command(name=name, args=args, target=target)


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
    """把正文解析为 ReplyDraft；整段正文直接作为 final_text。

    不再支持围栏 DSL（reasoning / tool 围栏已移除），IM 回复仅产生纯文本。
    """
    final_text = body.strip() or None
    return ReplyDraft(final_text=final_text)


def parse_message(text: str) -> tuple[str | None, ReplyDraft]:
    """一步解析完整 IM 消息：先剥离任务定位，再解析正文为 ReplyDraft。"""
    public_id, body = extract_task_target(text)
    return public_id, parse_reply(body)


def is_empty_draft(draft: ReplyDraft) -> bool:
    return not (draft.final_text and draft.final_text.strip())

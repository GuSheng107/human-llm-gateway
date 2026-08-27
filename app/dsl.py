import json
from dataclasses import dataclass

from .enums import EventKind


class DSLParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedEvent:
    kind: EventKind
    content: str = ""
    tool_name: str | None = None
    tool_args_json: str | None = None


def parse_human_reply(text: str, allow_plain: bool = True) -> list[ParsedEvent]:
    text = text.strip()
    if not text:
        raise DSLParseError("回复不能为空")
    if not text.startswith("/"):
        if allow_plain:
            return [ParsedEvent(EventKind.FINAL, text)]
        raise DSLParseError("回复必须以 /think、/tool、/reply 或 /done 开始")

    events: list[ParsedEvent] = []
    current: str | None = None
    buffer: list[str] = []
    done = False
    saw_tool = False
    saw_reply = False

    def flush() -> None:
        nonlocal buffer
        content = "\n".join(buffer).strip()
        if current == "think" and content:
            events.append(ParsedEvent(EventKind.REASONING, content))
        elif current == "reply" and content:
            events.append(ParsedEvent(EventKind.FINAL, content))
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "/done":
            if done:
                raise DSLParseError("不能重复使用 /done")
            flush()
            done = True
            current = None
            continue
        if line == "/think":
            if done:
                raise DSLParseError("/done 后不能继续添加内容")
            if saw_tool:
                raise DSLParseError("/tool 后不能继续添加 /think")
            flush()
            current = "think"
            continue
        if line == "/reply":
            if done:
                raise DSLParseError("/done 后不能继续添加内容")
            saw_reply = True
            flush()
            current = "reply"
            continue
        if line == "/tool" or line.startswith("/tool "):
            flush()
            if done:
                raise DSLParseError("/done 后不能继续添加工具调用")
            if saw_reply:
                raise DSLParseError("/reply 后不能继续添加 /tool")
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                raise DSLParseError("/tool 格式应为 /tool <name> <JSON>")
            try:
                json.loads(parts[2])
            except json.JSONDecodeError as exc:
                raise DSLParseError(f"tool 参数不是合法 JSON: {exc.msg}") from exc
            events.append(ParsedEvent(EventKind.TOOL_CALL, tool_name=parts[1], tool_args_json=parts[2]))
            saw_tool = True
            current = None
            continue
        if line.startswith("/"):
            raise DSLParseError(f"未知命令: {line.split(maxsplit=1)[0]}")
        if current is None:
            raise DSLParseError("文本必须位于 /think 或 /reply 段落中")
        buffer.append(raw_line)

    flush()
    if not done:
        raise DSLParseError("缺少 /done")
    if not events or not any(event.kind is EventKind.FINAL for event in events):
        raise DSLParseError("必须包含 /reply 最终回复")
    return events

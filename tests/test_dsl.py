import pytest

from app.dsl import DSLParseError, parse_human_reply
from app.enums import EventKind


def test_dsl_preserves_reasoning_tool_and_final_order():
    events = parse_human_reply(
        "/think\n分析请求\n/tool get_weather {\"city\":\"北京\"}\n/reply\n天气不错\n/done",
    )
    assert [event.kind for event in events] == [EventKind.REASONING, EventKind.TOOL_CALL, EventKind.FINAL]
    assert events[1].tool_name == "get_weather"


@pytest.mark.parametrize("text", ["/reply\n先回复\n/tool x {}\n/done", "/reply x", "/think\n缺 done"])
def test_dsl_rejects_invalid_sequence(text):
    with pytest.raises(DSLParseError):
        parse_human_reply(text)


def test_plain_reply_can_be_enabled_or_disabled():
    assert parse_human_reply("直接回复")[0].kind is EventKind.FINAL
    with pytest.raises(DSLParseError):
        parse_human_reply("直接回复", allow_plain=False)

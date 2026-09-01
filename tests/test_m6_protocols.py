"""M6 协议层测试：三协议解析、渲染、流式帧序列、上下文预算与历史链展开。"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.core.db as database
from app.core.constants import (
    MAX_CONTEXT_CHAIN_DEPTH,
    MAX_EXPANDED_CONTEXT_BYTES,
    MAX_EXPANDED_ITEMS,
)
from app.domain.enums import InferenceProtocol, TaskState
from app.domain.errors import DomainError, DomainErrorCode
from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import anthropic as anthropic_protocol
from app.protocols import chat_completions as chat_protocol
from app.protocols import responses as responses_protocol
from app.protocols.normalized import context_json_bytes, enforce_context_budget
from app.repositories.models import ApiKey, RequestTask, User
from app.repositories.tasks import TaskRepository
from app.services.inference_service import InferenceService


def _chat_payload(**extra: Any) -> dict[str, Any]:
    return {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}], **extra}


def _responses_payload(**extra: Any) -> dict[str, Any]:
    return {"model": "deepseek-v4-pro", "input": "hi", **extra}


def _anthropic_payload(**extra: Any) -> dict[str, Any]:
    return {
        "model": "deepseek-v4-pro",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
        **extra,
    }


_FULL_DRAFT = ReplyDraft(
    reasoning="先想一下",
    tool_calls=[ReplyToolCall(id="call_1", name="get_weather", arguments={"city": "北京"})],
    final_text="今天晴",
)


def _parse_error(payload: dict[str, Any], parser: Any) -> DomainError:
    with pytest.raises(DomainError) as exc:
        parser(json.dumps(payload, ensure_ascii=False).encode())
    return exc.value


# ----------------------------------------------------------------------
# Chat Completions 解析（§13）
# ----------------------------------------------------------------------


def test_chat_parse_rejects_store() -> None:
    error = _parse_error(_chat_payload(store=True), chat_protocol.parse_request)
    assert error.code is DomainErrorCode.UNSUPPORTED_PARAMETER
    assert error.status_code == 400


def test_chat_parse_rejects_bad_messages_or_model() -> None:
    for payload in (
        _chat_payload(messages=[]),
        _chat_payload(messages=["not-a-dict"]),
        _chat_payload(messages=[{"content": "缺少 role"}]),
        {"messages": [{"role": "user", "content": "hi"}]},
    ):
        error = _parse_error(payload, chat_protocol.parse_request)
        assert error.code is DomainErrorCode.INVALID_REQUEST
        assert error.status_code == 400


def test_chat_parse_invalid_json() -> None:
    for raw in (b"not-json", b"[1, 2]", b"null"):
        with pytest.raises(DomainError) as exc:
            chat_protocol.parse_request(raw)
        assert exc.value.code is DomainErrorCode.INVALID_REQUEST


def test_chat_parse_normalizes_stream_and_filters_options() -> None:
    parsed = chat_protocol.parse_request(
        json.dumps(_chat_payload(stream=True, temperature=0.7, weird_field="x")).encode()
    )
    assert parsed.stream is True
    normalized = parsed.normalized_request()
    # 未声明的透传字段保留在原始投影，但规范化 options 只收采样白名单。
    assert normalized["options"] == {"temperature": 0.7}
    assert parsed.options["weird_field"] == "x"


# ----------------------------------------------------------------------
# Responses 解析（§14）
# ----------------------------------------------------------------------


def test_responses_parse_rejects_server_control_fields() -> None:
    for field in ("background", "conversation", "store"):
        error = _parse_error(_responses_payload(**{field: True}), responses_protocol.parse_request)
        assert error.code is DomainErrorCode.UNSUPPORTED_PARAMETER
        assert error.status_code == 400


def test_responses_parse_rejects_bad_input() -> None:
    for payload in (
        _responses_payload(input=""),
        _responses_payload(input=[]),
        _responses_payload(input=123),
        _responses_payload(input={"key": "dict"}),
        _responses_payload(previous_response_id=42),
        _responses_payload(instructions=["not-str"]),
        {"input": "hi"},
    ):
        error = _parse_error(payload, responses_protocol.parse_request)
        assert error.code is DomainErrorCode.INVALID_REQUEST
        assert error.status_code == 400


def test_responses_parse_string_input_wraps_message_item() -> None:
    parsed = responses_protocol.parse_request(json.dumps(_responses_payload()).encode())
    assert parsed.base_context_items() == [{"type": "message", "role": "user", "content": "hi"}]
    assert parsed.previous_response_id is None


# ----------------------------------------------------------------------
# Anthropic 解析（§15）
# ----------------------------------------------------------------------


def test_anthropic_parse_requires_positive_max_tokens() -> None:
    for payload in (
        _anthropic_payload(max_tokens=None),
        _anthropic_payload(max_tokens=0),
        _anthropic_payload(max_tokens=-5),
        _anthropic_payload(max_tokens=True),
        _anthropic_payload(max_tokens="128"),
        _anthropic_payload(messages=[]),
        {"model": "deepseek-v4-pro", "max_tokens": 128, "messages": []},
    ):
        error = _parse_error(payload, anthropic_protocol.parse_request)
        assert error.code is DomainErrorCode.INVALID_REQUEST
        assert error.status_code == 400


def test_anthropic_parse_system_accepts_string_or_blocks() -> None:
    blocks = [{"type": "text", "text": "be brief"}]
    parsed = anthropic_protocol.parse_request(
        json.dumps(_anthropic_payload(system="be brief")).encode()
    )
    assert parsed.system == "be brief"
    parsed = anthropic_protocol.parse_request(
        json.dumps(_anthropic_payload(system=blocks)).encode()
    )
    assert parsed.system == blocks

    error = _parse_error(_anthropic_payload(system=123), anthropic_protocol.parse_request)
    assert error.code is DomainErrorCode.INVALID_REQUEST


# ----------------------------------------------------------------------
# 渲染器（§13.2 / §14.2 / §15.2）
# ----------------------------------------------------------------------


def test_chat_render_response_full_draft() -> None:
    body = chat_protocol.render_response("deepseek-v4-pro", _FULL_DRAFT)
    assert body["object"] == "chat.completion"
    assert body["model"] == "deepseek-v4-pro"
    message = body["choices"][0]["message"]
    assert message["role"] == "assistant"
    assert message["content"] == "今天晴"
    assert message["reasoning_content"] == "先想一下"
    call = message["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "北京"}
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}


def test_chat_render_response_text_only_stops() -> None:
    body = chat_protocol.render_response("deepseek-v4-pro", ReplyDraft(final_text="ok"))
    message = body["choices"][0]["message"]
    assert "reasoning_content" not in message
    assert "tool_calls" not in message
    assert body["choices"][0]["finish_reason"] == "stop"


def test_chat_stream_frames_order_and_done() -> None:
    frames = list(chat_protocol.stream_frames("deepseek-v4-pro", _FULL_DRAFT))
    assert frames[-1].data == "[DONE]"
    payloads = [json.loads(frame.data) for frame in frames[:-1]]
    deltas = [item["choices"][0]["delta"] for item in payloads]
    assert deltas[0] == {"role": "assistant"}
    assert any("reasoning_content" in delta for delta in deltas)
    assert any("tool_calls" in delta for delta in deltas)
    assert any(delta.get("content") == "今天晴" for delta in deltas)
    assert payloads[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert all(item["model"] == "deepseek-v4-pro" for item in payloads)


def test_responses_output_items_order() -> None:
    items, message_item = responses_protocol.reply_output_items(_FULL_DRAFT)
    assert [item["type"] for item in items] == ["reasoning", "function_call", "message"]
    assert items[0]["summary"][0]["text"] == "先想一下"
    assert items[1]["name"] == "get_weather"
    assert json.loads(items[1]["arguments"]) == {"city": "北京"}
    assert message_item["content"][0]["text"] == "今天晴"


def test_responses_render_and_stream_events() -> None:
    usage = {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}
    body = responses_protocol.render_response(
        "deepseek-v4-pro", "resp_abc", _FULL_DRAFT, usage=usage
    )
    assert body["id"] == "resp_abc"
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert [item["type"] for item in body["output"]] == ["reasoning", "function_call", "message"]
    assert body["usage"] == usage
    # 消息内容块带 annotations（官方 SDK 必需字段）。
    message = next(item for item in body["output"] if item["type"] == "message")
    assert message["content"][0]["annotations"] == []
    assert message["id"]

    events = list(
        responses_protocol.stream_events("deepseek-v4-pro", "resp_abc", _FULL_DRAFT, usage=usage)
    )
    names = [event.event for event in events]
    assert names[0] == "response.created"
    assert names[1] == "response.in_progress"
    assert names[-1] == "response.completed"
    for event in events:
        assert json.loads(event.data)["type"] == event.event
    # 流式补齐 content part / delta / done 事件（官方 SDK 契约）。
    assert "response.content_part.added" in names
    assert "response.output_text.delta" in names
    assert "response.output_text.done" in names
    assert "response.content_part.done" in names
    assert "response.function_call_arguments.delta" in names
    assert "response.function_call_arguments.done" in names
    # sequence_number 逐事件递增且每帧必带。
    seqs = [json.loads(event.data)["sequence_number"] for event in events]
    assert seqs == sorted(seqs)
    added = [
        json.loads(event.data) for event in events if event.event == "response.output_item.added"
    ]
    assert [item["item"]["type"] for item in added] == ["reasoning", "function_call", "message"]
    completed = json.loads(events[-1].data)
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["usage"] == usage


def test_chat_completions_stable_id_created_and_usage() -> None:
    """同一次流式响应保持稳定 id 与 created；include_usage 追加 usage 帧。"""
    usage = {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}
    frames = list(
        chat_protocol.stream_frames("deepseek-v4-pro", _FULL_DRAFT, usage=usage, include_usage=True)
    )
    payloads = [json.loads(frame.data) for frame in frames[:-1]]
    ids = {item["id"] for item in payloads}
    created = {item["created"] for item in payloads}
    assert len(ids) == 1
    assert len(created) == 1
    # finish 帧后是带 usage 的空 choices 帧，最后是 [DONE]。
    usage_frames = [item for item in payloads if item.get("usage") is not None]
    assert len(usage_frames) == 1
    assert usage_frames[0]["choices"] == []
    assert usage_frames[0]["usage"] == usage
    assert frames[-1].data == "[DONE]"
    # 未请求 include_usage 时不追加 usage 帧。
    frames_no_usage = list(chat_protocol.stream_frames("m", _FULL_DRAFT))
    payloads_no_usage = [json.loads(frame.data) for frame in frames_no_usage[:-1]]
    assert all(item.get("usage") is None for item in payloads_no_usage)


def test_anthropic_render_response_full_draft() -> None:
    # 人工路径不返回 thinking block：Anthropic 签名无法伪造（决策 §15.2）。
    body = anthropic_protocol.render_response("deepseek-v4-pro", _FULL_DRAFT)
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert [block["type"] for block in body["content"]] == ["tool_use", "text"]
    assert body["content"][0]["name"] == "get_weather"
    assert body["content"][0]["input"] == {"city": "北京"}
    assert body["content"][1]["text"] == "今天晴"
    assert body["stop_reason"] == "tool_use"
    # 未传 usage 时输出 0；调用方（API 层）总是计算快照后传入。
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0}
    # 传入 usage 时按快照输出，不再使用硬编码假值。
    body_with_usage = anthropic_protocol.render_response(
        "deepseek-v4-pro", _FULL_DRAFT, usage={"input_tokens": 11, "output_tokens": 22}
    )
    assert body_with_usage["usage"] == {"input_tokens": 11, "output_tokens": 22}


def test_anthropic_stream_events_order() -> None:
    events = list(
        anthropic_protocol.stream_events(
            "deepseek-v4-pro",
            _FULL_DRAFT,
            usage={"input_tokens": 11, "output_tokens": 22},
        )
    )
    names = [event.event for event in events]
    assert names[0] == "message_start"
    assert names[-1] == "message_stop"
    # thinking 块被省略：只有 tool_use 与 text 两个块。
    assert names.count("content_block_start") == 2
    assert names.count("content_block_stop") == 2
    deltas = [
        json.loads(event.data)["delta"]["type"]
        for event in events
        if event.event == "content_block_delta"
    ]
    assert deltas == ["input_json_delta", "text_delta"]
    message_start = next(
        json.loads(event.data) for event in events if event.event == "message_start"
    )
    assert message_start["message"]["usage"]["input_tokens"] == 11
    message_delta = next(
        json.loads(event.data) for event in events if event.event == "message_delta"
    )
    assert message_delta["delta"]["stop_reason"] == "tool_use"
    assert message_delta["usage"]["output_tokens"] == 22


def test_stream_error_frames_contract() -> None:
    chat_error = json.loads(chat_protocol.stream_error_frame("boom").data)
    assert chat_error["error"]["type"] == "server_error"
    assert chat_error["error"]["message"] == "boom"

    responses_error = responses_protocol.stream_error_event("resp_abc", "m", "boom")
    assert responses_error.event == "response.failed"
    payload = json.loads(responses_error.data)
    assert payload["response"]["id"] == "resp_abc"
    assert payload["response"]["status"] == "failed"
    assert payload["response"]["error"]["code"] == "server_error"

    anthropic_error = anthropic_protocol.stream_error_event("boom")
    assert anthropic_error.event == "error"
    payload = json.loads(anthropic_error.data)
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "api_error"


# ----------------------------------------------------------------------
# 上下文预算（§12.5 三重上限中的条目与字节）
# ----------------------------------------------------------------------


def test_context_budget_rejects_item_overflow() -> None:
    items = [{"type": "message"} for _ in range(MAX_EXPANDED_ITEMS + 1)]
    with pytest.raises(DomainError) as exc:
        enforce_context_budget(items)
    assert exc.value.code is DomainErrorCode.CONTEXT_LENGTH_EXCEEDED
    assert exc.value.status_code == 400


def test_context_budget_rejects_byte_overflow() -> None:
    items = [{"padding": "x" * 8192} for _ in range(MAX_EXPANDED_ITEMS)]
    assert context_json_bytes(items) > MAX_EXPANDED_CONTEXT_BYTES
    with pytest.raises(DomainError) as exc:
        enforce_context_budget(items)
    assert exc.value.code is DomainErrorCode.CONTEXT_LENGTH_EXCEEDED


def test_context_budget_boundary_passes() -> None:
    enforce_context_budget([{"i": index} for index in range(MAX_EXPANDED_ITEMS)])


# ----------------------------------------------------------------------
# previous_response_id 链展开（§12.5，唯一语义）
# ----------------------------------------------------------------------


def _create_responses_task(key_id: int, owner_id: int, payload: dict[str, Any]) -> RequestTask:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    parsed = responses_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        task = InferenceService().create_task(
            session,
            key=session.get(ApiKey, key_id),
            owner=session.get(User, owner_id),
            protocol=InferenceProtocol.OPENAI_RESPONSES,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        return task


def _complete_task(task_id: int, owner_id: int) -> None:
    payload = ReplyDraft(final_text="parent-reply").model_dump_json(exclude_none=True)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        assert TaskRepository().first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=owner_id,
            expected_version=task.version,
            response_payload_json=payload,
        )
        session.commit()
    with database.SessionLocal() as session:
        assert InferenceService().finalize(
            session, session.get(RequestTask, task_id), TaskState.COMPLETED
        )
        session.commit()


def _normalized_context(task_id: int) -> list[Any]:
    with database.SessionLocal() as session:
        return json.loads(session.get(RequestTask, task_id).normalized_request_json)["context"]


def test_previous_response_id_expands_parent_context(client, created_user, created_key) -> None:
    first = _create_responses_task(
        created_key.id,
        created_user.user_id,
        _responses_payload(input=[{"type": "message", "role": "user", "content": "第一轮"}]),
    )
    _complete_task(first.id, created_user.user_id)

    second = _create_responses_task(
        created_key.id,
        created_user.user_id,
        _responses_payload(previous_response_id=first.response_public_id, input="第二轮"),
    )

    context = _normalized_context(second.id)
    # 唯一语义：父 context + 父回复输出项 + 本轮输入，各出现一次。
    assert context[0] == {"type": "message", "role": "user", "content": "第一轮"}
    assert context[1]["type"] == "message"
    assert context[1]["role"] == "assistant"
    assert context[1]["content"][0]["text"] == "parent-reply"
    assert context[2] == {"type": "message", "role": "user", "content": "第二轮"}
    assert second.previous_task_id == first.id


def test_previous_response_id_rejects_unknown_or_unfinished(
    client, created_user, created_key
) -> None:
    with pytest.raises(DomainError) as exc:
        _create_responses_task(
            created_key.id,
            created_user.user_id,
            _responses_payload(previous_response_id="resp_missing"),
        )
    assert exc.value.public_code == "invalid_previous_response_id"

    waiting = _create_responses_task(created_key.id, created_user.user_id, _responses_payload())
    with pytest.raises(DomainError) as exc:
        _create_responses_task(
            created_key.id,
            created_user.user_id,
            _responses_payload(previous_response_id=waiting.response_public_id),
        )
    assert exc.value.public_code == "invalid_previous_response_id"


def test_previous_response_id_rejects_cross_key(client, created_user, created_key) -> None:
    completed = _create_responses_task(created_key.id, created_user.user_id, _responses_payload())
    _complete_task(completed.id, created_user.user_id)

    created = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={"name": "k2", "delivery_mode": "web", "reply_strategy": "human"},
    )
    assert created.status_code == 201, created.text
    other_key_id = int(created.json()["id"])

    with pytest.raises(DomainError) as exc:
        _create_responses_task(
            other_key_id,
            created_user.user_id,
            _responses_payload(previous_response_id=completed.response_public_id),
        )
    assert exc.value.public_code == "invalid_previous_response_id"


def test_previous_response_id_chain_depth_limit(client, created_user, created_key) -> None:
    task = _create_responses_task(created_key.id, created_user.user_id, _responses_payload())
    _complete_task(task.id, created_user.user_id)
    # 构造 22 个 COMPLETED 任务的链；第 23 个引用链尾时祖先数超过深度上限。
    for _ in range(MAX_CONTEXT_CHAIN_DEPTH + 1):
        following = _create_responses_task(
            created_key.id,
            created_user.user_id,
            _responses_payload(previous_response_id=task.response_public_id),
        )
        _complete_task(following.id, created_user.user_id)
        task = following

    with pytest.raises(DomainError) as exc:
        _create_responses_task(
            created_key.id,
            created_user.user_id,
            _responses_payload(previous_response_id=task.response_public_id),
        )
    assert exc.value.code is DomainErrorCode.CONTEXT_LENGTH_EXCEEDED


def test_previous_response_id_expansion_respects_item_budget(
    client, created_user, created_key
) -> None:
    parent_input = [
        {"type": "message", "role": "user", "content": "m"} for _ in range(MAX_EXPANDED_ITEMS)
    ]
    parent = _create_responses_task(
        created_key.id, created_user.user_id, _responses_payload(input=parent_input)
    )
    _complete_task(parent.id, created_user.user_id)

    # 父 context 512 + 父回复输出 1 + 本轮输入 1 = 514 > 512。
    with pytest.raises(DomainError) as exc:
        _create_responses_task(
            created_key.id,
            created_user.user_id,
            _responses_payload(previous_response_id=parent.response_public_id),
        )
    assert exc.value.code is DomainErrorCode.CONTEXT_LENGTH_EXCEEDED

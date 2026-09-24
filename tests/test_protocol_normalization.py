"""真实 HTTP 边界的 3×3×2 协议矩阵、工具历史与字段保真回归。"""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

import app.core.db as database
from app.domain.enums import InferenceProtocol, LLMProtocol, TaskState
from app.domain.errors import DomainError, DomainErrorCode
from app.protocols import anthropic, chat_completions, cross, responses, upstream_reply
from app.repositories.models import RequestTask, User
from app.services.llm_forward_service import LlmForwardService
from tests.test_llm_upstream_stream import FragmentedSSE, _text, _tool_stream

PROTOCOLS = ("openai_chat", "openai_responses", "anthropic_messages")
PATHS = dict(zip(PROTOCOLS, ("/v1/chat/completions", "/v1/responses", "/v1/messages")))
PARSERS = dict(zip(PROTOCOLS, (chat_completions, responses, anthropic)))
BUILDERS = dict(
    zip(PROTOCOLS, (cross.to_chat_request, cross.to_responses_request, cross.to_anthropic_request))
)
SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


def _payload(protocol):
    function = {"name": "weather", "description": "天气", "parameters": SCHEMA}
    base = {"model": "deepseek-v4-pro", "temperature": 0.5}
    if protocol == "openai_chat":
        return {
            **base,
            "messages": [
                {"role": "system", "content": "system-rules"},
                {"role": "user", "content": "old question"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_old",
                            "type": "function",
                            "function": {"name": "weather", "arguments": '{"city":"上海"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_old", "content": "old result"},
                {"role": "user", "content": "北京呢"},
            ],
            "tools": [{"type": "function", "function": function}],
            "tool_choice": {"type": "function", "function": {"name": "weather"}},
            "parallel_tool_calls": False,
            "max_completion_tokens": 128,
        }
    if protocol == "openai_responses":
        return {
            **base,
            "instructions": "system-rules",
            "input": [
                {"role": "user", "content": "old question"},
                {
                    "type": "function_call",
                    "id": "fc_item_old",
                    "call_id": "call_old",
                    "name": "weather",
                    "arguments": '{"city":"上海"}',
                },
                {"type": "function_call_output", "call_id": "call_old", "output": "old result"},
                {"role": "user", "content": [{"type": "input_text", "text": "北京呢"}]},
            ],
            "tools": [{"type": "function", **function}],
            "tool_choice": {"type": "function", "name": "weather"},
            "parallel_tool_calls": False,
            "max_output_tokens": 128,
        }
    return {
        **base,
        "system": "system-rules",
        "messages": [
            {"role": "user", "content": "old question"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_old",
                        "name": "weather",
                        "input": {"city": "上海"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_old", "content": "old result"},
                    {"type": "text", "text": "北京呢"},
                ],
            },
        ],
        "tools": [{"name": "weather", "description": "天气", "input_schema": SCHEMA}],
        "tool_choice": {"type": "tool", "name": "weather", "disable_parallel_tool_use": True},
        "max_tokens": 128,
    }


def _normalized(protocol, raw):
    parsed = PARSERS[protocol].parse_request(json.dumps(raw).encode())
    return (
        parsed.normalized_request(parsed.base_context_items())
        if protocol == "openai_responses"
        else parsed.normalized_request()
    )


def _upstream(protocol, arguments=None):
    arguments = {"city": "北京"} if arguments is None else arguments
    if protocol == "openai_chat":
        return {
            "choices": [
                {
                    "message": {
                        "content": "answer",
                        "tool_calls": [
                            {
                                "type": "function",
                                "id": "call_weather",
                                "function": {"name": "weather", "arguments": json.dumps(arguments)},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    if protocol == "openai_responses":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_item",
                    "call_id": "call_weather",
                    "name": "weather",
                    "arguments": json.dumps(arguments),
                },
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
        }
    return {
        "content": [
            {"type": "tool_use", "id": "call_weather", "name": "weather", "input": arguments},
            {"type": "text", "text": "answer"},
        ],
        "stop_reason": "tool_use",
    }


@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("target", PROTOCOLS)
@pytest.mark.parametrize("stream", [False, True])
def test_http_protocol_matrix(client, created_user, monkeypatch, source, target, stream):
    cfg = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "matrix",
            "protocol": target,
            "base_url": "https://upstream.example.com/v1",
            "api_key": "test-only-secret",
            "model": "real-model",
            "enabled": True,
        },
    )
    assert cfg.status_code == 201, cfg.text
    key = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={
            "name": "matrix",
            "delivery_mode": "web",
            "reply_strategy": "llm",
            "llm_config_id": cfg.json()["id"],
        },
    )
    assert key.status_code == 201
    raw = {**_payload(source), "stream": stream}
    if source == target:
        raw["vendor_extension"] = {"opaque": [1, 2]}
    real_client = httpx.AsyncClient
    captured = []

    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        assert request.url.path == PATHS[target]
        if stream:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=FragmentedSSE(_text(target, "answer") + _tool_stream(target)),
            )
        return httpx.Response(200, json=_upstream(target))

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *, timeout: real_client(transport=httpx.MockTransport(respond), timeout=timeout),
    )
    response = client.post(
        PATHS[source], headers={"Authorization": f"Bearer {key.json()['plaintext']}"}, json=raw
    )
    assert response.status_code == 200, response.text
    assert "deepseek-v4-pro" in response.text
    assert "real-model" not in response.text
    assert len(captured) == 1
    body = captured[0]
    assert body["model"] == "real-model"
    assert body.get("stream", False) is stream
    if source == target:
        assert body["vendor_extension"] == raw["vendor_extension"]
    if target == "openai_chat":
        assert body["tools"][0]["function"]["parameters"] == SCHEMA
        assert any(
            item.get("tool_call_id") == "call_old" and item["content"] == "old result"
            for item in body["messages"]
        )
        assert body["tool_choice"] == {"type": "function", "function": {"name": "weather"}}
        assert body["parallel_tool_calls"] is False
    elif target == "openai_responses":
        assert body["tools"][0]["parameters"] == SCHEMA
        assert "function" not in body["tools"][0]
        assert any(
            item.get("type") == "function_call_output"
            and item["call_id"] == "call_old"
            and item["output"] == "old result"
            for item in body["input"]
        )
        assert body["tool_choice"] == {"type": "function", "name": "weather"}
        assert body["parallel_tool_calls"] is False
    else:
        assert body["tools"][0]["input_schema"] == SCHEMA
        assert body["tool_choice"] == {
            "type": "tool",
            "name": "weather",
            "disable_parallel_tool_use": True,
        }
        assert body["max_tokens"] == 128
    wire = json.dumps(body, ensure_ascii=False)
    assert wire.count("system-rules") == 1
    assert "call_old" in wire and "上海" in wire and "old result" in wire
    with database.SessionLocal() as session:
        task = session.scalars(
            select(RequestTask).where(RequestTask.api_key_id == int(key.json()["id"]))
        ).one()
        assert json.loads(task.raw_payload_json) == raw
        assert task.state is TaskState.COMPLETED
        assert json.loads(task.response_payload_json)["tool_calls"] == [
            {"id": "call_weather", "name": "weather", "arguments": {"city": "北京"}}
        ]
        assert task.slot_released_at is not None
        assert session.get(User, created_user.user_id).active_task_count == 0


@pytest.mark.parametrize("source,target", [(s, t) for s in PROTOCOLS for t in PROTOCOLS if s != t])
@pytest.mark.parametrize("extension", ["top", "message", "content", "tool", "choice"])
def test_cross_rejects_unknown_fields_without_mutating_raw(source, target, extension):
    raw = _payload(source)
    if extension == "top":
        raw["vendor_extra"] = {"keep": "me"}
    elif extension == "message":
        raw["input" if source == "openai_responses" else "messages"][0]["vendor_extra"] = 1
    elif extension == "content":
        raw["input" if source == "openai_responses" else "messages"][0]["content"] = [
            {"type": "text", "text": "hi", "vendor_extra": 1}
        ]
    elif extension == "tool":
        raw["tools"][0]["vendor_extra"] = 1
    else:
        raw["tool_choice"]["vendor_extra"] = 1
    original = copy.deepcopy(raw)
    with pytest.raises(DomainError) as exc:
        BUILDERS[target](_normalized(source, raw), "real")
    assert exc.value.code is DomainErrorCode.UNSUPPORTED_PARAMETER
    assert exc.value.status_code == 400
    assert raw == original


def test_previous_response_expands_input_without_losing_extensions():
    raw = _payload("openai_responses")
    raw.update(previous_response_id="resp_gateway", vendor_extra={"x": 1}, store=False)
    normalized = _normalized("openai_responses", raw)
    normalized["context"].insert(0, {"role": "user", "content": "ancestor"})
    task = SimpleNamespace(
        protocol=InferenceProtocol.OPENAI_RESPONSES,
        raw_payload_json=json.dumps(raw),
        requested_model="fake",
    )
    cfg = SimpleNamespace(
        protocol=LLMProtocol.OPENAI_RESPONSES,
        real_model="real",
        extra_body={},
        default_temperature=None,
        default_top_p=None,
        default_top_k=None,
        max_output_tokens=None,
        thinking_mode=None,
    )
    body = LlmForwardService().build_upstream_request(task, cfg, None, normalized)
    assert "previous_response_id" not in body
    assert body["input"][0]["content"] == "ancestor"
    assert body["vendor_extra"] == {"x": 1}
    assert body["store"] is False
    assert json.loads(task.raw_payload_json) == raw


@pytest.mark.parametrize("target", PROTOCOLS)
@pytest.mark.parametrize("arguments", ["broken", [], 1, None])
def test_nonstream_corrupt_arguments_never_become_empty_object(target, arguments):
    body = _upstream(target)
    if target == "openai_chat":
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    elif target == "openai_responses":
        body["output"][0]["arguments"] = arguments
    else:
        body["content"][0]["input"] = arguments
    parser = {
        "openai_chat": upstream_reply.parse_chat_response,
        "openai_responses": upstream_reply.parse_responses_response,
        "anthropic_messages": upstream_reply.parse_anthropic_response,
    }[target]
    with pytest.raises(DomainError) as exc:
        parser(body)
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


def test_openai_structured_output_round_trip():
    chat_format = {
        "type": "json_schema",
        "json_schema": {"name": "weather", "schema": SCHEMA, "strict": True},
    }
    normalized = _normalized(
        "openai_chat", {**_payload("openai_chat"), "response_format": chat_format}
    )
    converted = cross.to_responses_request(normalized, "real")
    assert converted["text"]["format"] == {"type": "json_schema", **chat_format["json_schema"]}
    back = cross.to_chat_request(_normalized("openai_responses", converted), "real")
    assert back["response_format"] == chat_format


@pytest.mark.parametrize("field", ["stop", "stop_sequences"])
def test_responses_rejects_stop_without_an_equivalent(field):
    with pytest.raises(DomainError):
        cross.to_responses_request({"context": [], "options": {field: ["END"]}}, "real")


def test_conversion_trace_records_actions_without_values(monkeypatch):
    records = []
    monkeypatch.setattr(cross, "log_event", lambda *args, **fields: records.append(fields))
    raw = {
        **_payload("openai_chat"),
        "user": "PRIVATE_USER_VALUE",
        "stream_options": {"include_usage": True},
    }
    cross.to_responses_request(_normalized("openai_chat", raw), "real")
    assert len(records) == 1
    assert "PRIVATE_USER_VALUE" not in json.dumps(records)
    assert "system-rules" not in json.dumps(records)
    actions = {entry["field"]: entry["action"] for entry in records[0]["field_actions"]}
    assert actions["stream_options"] == "consume"
    assert actions["tools"] == "convert"


@pytest.mark.parametrize("source,target", [(s, t) for s in PROTOCOLS for t in PROTOCOLS if s != t])
def test_api_cross_rejection_is_400_and_does_not_call_upstream(
    client, created_user, monkeypatch, source, target
):
    cfg = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "reject",
            "protocol": target,
            "base_url": "https://upstream.example.com/v1",
            "api_key": "test-only-secret",
            "model": "real-model",
            "enabled": True,
        },
    ).json()
    key = client.post(
        "/api/api-keys",
        headers=created_user.headers,
        json={
            "name": "reject",
            "delivery_mode": "web",
            "reply_strategy": "llm",
            "llm_config_id": cfg["id"],
        },
    ).json()

    def unexpected(**kwargs):
        pytest.fail("unsupported field reached upstream")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    response = client.post(
        PATHS[source],
        headers={"Authorization": f"Bearer {key['plaintext']}"},
        json={**_payload(source), "vendor_extra": 1},
    )
    assert response.status_code == 400, response.text
    assert (
        "unsupported_parameter" in response.text
        if source != "anthropic_messages"
        else response.json()["error"]["type"] == "invalid_request_error"
    )
    with database.SessionLocal() as session:
        task = session.scalars(
            select(RequestTask).where(RequestTask.api_key_id == int(key["id"]))
        ).one()
        assert task.state is TaskState.FAILED
        assert task.response_payload_json is None
        assert session.get(User, created_user.user_id).active_task_count == 0


def test_responses_chain_preserves_opaque_options_in_auto_and_draft(
    client, created_user, monkeypatch
):
    from tests.test_m7_llm_forward import _create_llm_config, _create_strategy_key, _llm_body

    cfg = _create_llm_config(client, created_user.headers, _llm_body(protocol="openai_responses"))
    key = _create_strategy_key(
        client, created_user.headers, strategy="llm", llm_config_id=int(cfg["id"])
    )
    captured = []

    async def respond(**kwargs):
        captured.append(kwargs["request_body"])
        return _upstream("openai_responses")

    monkeypatch.setattr("app.services.llm_upstream.post_responses", respond)
    headers = {"Authorization": f"Bearer {key['plaintext']}"}
    first = client.post("/v1/responses", headers=headers, json=_payload("openai_responses"))
    assert first.status_code == 200, first.text
    second_raw = {
        **_payload("openai_responses"),
        "input": [{"type": "function_call_output", "call_id": "call_weather", "output": "北京晴"}],
        "previous_response_id": first.json()["id"],
        "store": False,
        "vendor_extra": {"trace": "opaque"},
    }
    second = client.post("/v1/responses", headers=headers, json=second_raw)
    assert second.status_code == 200, second.text
    assert captured[1]["vendor_extra"] == {"trace": "opaque"}
    assert captured[1]["store"] is False
    assert "previous_response_id" not in captured[1]
    calls = [item for item in captured[1]["input"] if item.get("type") == "function_call"]
    assert any(item["call_id"] == "call_weather" for item in calls)
    # 手动生成使用同样的原始请求和已展开上下文，不应丢弃扩展字段。
    from app.domain.enums import ReplyStrategy
    from app.repositories.models import ApiKey
    from app.services.inference_service import InferenceService

    with database.SessionLocal() as session:
        key_row = session.get(ApiKey, int(key["id"]))
        key_row.reply_strategy = ReplyStrategy.HUMAN
        key_row.llm_config_id = None
        encoded = json.dumps(second_raw).encode()
        task = InferenceService().create_task(
            session,
            key=key_row,
            owner=session.get(User, created_user.user_id),
            protocol=InferenceProtocol.OPENAI_RESPONSES,
            parsed=responses.parse_request(encoded),
            raw_body=encoded,
            headers={},
        )
        task_id = task.id
        session.commit()
    draft = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": cfg["id"]},
    )
    assert draft.status_code == 201, draft.text
    assert captured[2]["vendor_extra"] == {"trace": "opaque"}
    assert captured[2]["input"] == captured[1]["input"]
    assert draft.json()["tool_calls"][0]["id"] == "call_weather"

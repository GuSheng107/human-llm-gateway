"""小助手三协议工具闭环、MCP 同入口校验与敏感信息边界。"""

import asyncio
import json
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

import app.core.db as database
from app.domain.enums import AuditAction, AuditResult, LLMProtocol
from app.domain.errors import DomainError
from app.protocols.assistant import build_request, ensure_anthropic_budget
from app.repositories.models import AuditLog, RequestTask, TaskDraft, TaskInboxState
from app.services.assistant.redaction import build_page_context, redact_text, redact_value
from app.services.assistant.service import AssistantService
from app.services.mcp.tools import get_mcp_tool
from tests.test_m6_tasks import _make_waiting_task
from tests.test_m8_assistant import _llm_body, _make_session, _seed_long_history


def _setup(client, user, protocol):
    body = {**_llm_body(), "protocol": protocol}
    response = client.post("/api/llm-configs", headers=user.headers, json=body)
    assert response.status_code == 201, response.text
    row = _make_session(client, user.headers, int(response.json()["id"]))
    return f"/api/assistant/sessions/{row['id']}"


def _reply(protocol, calls=True, name="list_models", arguments="{}"):
    if protocol == "openai_chat":
        message = {"content": "中间轮正文" if calls else "最终建议"}
        if calls:
            message["tool_calls"] = [
                {
                    "type": "function",
                    "id": "call_42",
                    "function": {"name": name, "arguments": arguments},
                }
            ]
        return {
            "choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
    if protocol == "openai_responses":
        output = [
            {
                "type": "message",
                "id": "msg_42",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "中间轮正文" if calls else "最终建议"}],
            }
        ]
        if calls:
            output.extend(
                [
                    {
                        "type": "reasoning",
                        "id": "rs_42",
                        "summary": [],
                        "encrypted_content": "opaque-reasoning",
                    },
                    {
                        "type": "function_call",
                        "id": "fc_42",
                        "call_id": "call_42",
                        "name": name,
                        "arguments": arguments,
                        "status": "completed",
                    },
                ]
            )
        return {
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }
    content = [{"type": "text", "text": "中间轮正文" if calls else "最终建议"}]
    if calls:
        content.extend(
            [
                {"type": "thinking", "thinking": "内部推理", "signature": "opaque-signature"},
                {"type": "tool_use", "id": "call_42", "name": name, "input": json.loads(arguments)},
            ]
        )
    return {
        "content": content,
        "stop_reason": "tool_use" if calls else "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def _event(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _wire(protocol, payload):
    if protocol == "openai_chat":
        message = deepcopy(payload["choices"][0]["message"])
        for index, call in enumerate(message.get("tool_calls", [])):
            call["index"] = index
        return _event({"choices": [{"delta": message}]}) + "data: [DONE]\n\n"
    if protocol == "openai_responses":
        return "".join(
            _event({"type": "response.output_item.done", "item": item})
            for item in payload["output"]
        ) + _event({"type": "response.completed", "response": payload})
    wire = _event({"type": "message_start", "message": {"usage": payload["usage"]}})
    for index, block in enumerate(payload["content"]):
        if block["type"] == "thinking":
            wire += _event(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                }
            )
            for kind, field in (("thinking_delta", "thinking"), ("signature_delta", "signature")):
                wire += _event(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": kind, field: block[field]},
                    }
                )
        else:
            wire += _event({"type": "content_block_start", "index": index, "content_block": block})
        wire += _event({"type": "content_block_stop", "index": index})
    return wire + _event({"type": "message_stop"})


@pytest.mark.parametrize("protocol", [p.value for p in LLMProtocol])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("parallel", [False, True])
def test_native_tool_loop_real_http(client, created_user, monkeypatch, protocol, stream, parallel):
    url = _setup(client, created_user, protocol)
    captured = []
    real_client = httpx.AsyncClient

    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        response = _reply(protocol, calls=len(captured) == 1)
        if parallel and len(captured) == 1:
            if protocol == "openai_chat":
                calls = response["choices"][0]["message"]["tool_calls"]
                calls.append({**deepcopy(calls[0]), "id": "call_43"})
            elif protocol == "openai_responses":
                response["output"].append(
                    {**deepcopy(response["output"][-1]), "id": "fc_43", "call_id": "call_43"}
                )
            else:
                response["content"].append({**deepcopy(response["content"][-1]), "id": "call_43"})
        if stream:
            return httpx.Response(
                200, text=_wire(protocol, response), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    response = client.post(
        url + "/messages" + ("/stream" if stream else ""),
        headers=created_user.headers,
        json={"text": "查询模型"},
    )
    assert response.status_code == (200 if stream else 201), response.text
    assert "最终建议" in response.text
    assert "中间轮正文" not in response.text
    assert len(captured) == 2
    first, second = captured
    if protocol == "openai_chat":
        assert first["tools"][0]["function"]["parameters"]["type"] == "object"
        assert second["messages"][-1]["tool_call_id"] == ("call_43" if parallel else "call_42")
    elif protocol == "openai_responses":
        assert first["tools"][0]["type"] == "function" and "function" not in first["tools"][0]
        assert "messages" not in second
        assert second["input"][-1]["type"] == "function_call_output"
        assert second["input"][-1]["call_id"] == ("call_43" if parallel else "call_42")
        assert any(item.get("encrypted_content") == "opaque-reasoning" for item in second["input"])
    else:
        assert "input_schema" in first["tools"][0]
        assert second["messages"][-1]["content"][0]["tool_use_id"] == "call_42"
        assert any(
            item.get("signature") == "opaque-signature"
            for item in second["messages"][-2]["content"]
        )
    detail = client.get(url, headers=created_user.headers).json()
    assert len(detail["messages"]) == 2
    assert "opaque-" not in json.dumps(detail)
    with database.SessionLocal() as db:
        audits = list(
            db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value))
        )
        assert len(audits) == (2 if parallel else 1) and all(
            a.result is AuditResult.SUCCESS for a in audits
        )
        assert audits[0].request_id


@pytest.mark.parametrize("protocol", [p.value for p in LLMProtocol])
def test_summary_uses_native_request_without_tools(client, created_user, protocol):
    url = _setup(client, created_user, protocol)
    _seed_long_history(client, int(url.rsplit("/", 1)[-1]))
    captured = []

    async def post(self, proto, base, secret, body, timeout, **kwargs):
        captured.append(deepcopy(body))
        return _reply(protocol, calls=False)

    with patch.object(AssistantService, "_post_upstream", post):
        response = client.post(
            url + "/messages", headers=created_user.headers, json={"text": "继续"}
        )
    assert response.status_code == 201
    assert captured[0]["tools"] == []
    assert ("input" in captured[0]) == (protocol == "openai_responses")
    assert ("system" in captured[0]) == (protocol == "anthropic_messages")


@pytest.mark.parametrize("stream", [False, True])
def test_round_limit_is_failure_without_empty_success(client, created_user, stream):
    url = _setup(client, created_user, "openai_chat")
    count = 0

    async def post(*args, **kwargs):
        nonlocal count
        count += 1
        return _reply("openai_chat")

    async def chunks(*args, **kwargs):
        from app.services.llm_upstream import UpstreamChunk

        nonlocal count
        count += 1
        yield UpstreamChunk(
            text="中间轮正文", tool_call={"id": "call_42", "name": "list_models", "arguments": {}}
        )

    with (
        patch.object(AssistantService, "_post_upstream", post),
        patch.object(AssistantService, "_stream_upstream", chunks),
    ):
        response = client.post(
            url + "/messages" + ("/stream" if stream else ""),
            headers=created_user.headers,
            json={"text": "查询"},
        )
    assert count == 5
    assert "中间轮正文" not in response.text
    assert response.status_code == (200 if stream else 502)
    if stream:
        assert '"type": "error"' in response.text and '"type": "done"' not in response.text
    assert len(client.get(url, headers=created_user.headers).json()["messages"]) == 1
    with database.SessionLocal() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
                    )
                )
            )
            == 4
        )


def _rpc(client, headers, name, arguments):
    return client.post(
        "/api/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("missing", {}),
        ("list_tasks", []),
        ("list_tasks", {"page": 0}),
        ("list_tasks", {"page": True}),
        ("list_tasks", {"page_size": 101}),
        ("list_tasks", {"undeclared": "secret-plain-value"}),
        ("get_task_detail", {}),
    ],
)
def test_mcp_rejects_invalid_parameters_and_audits_failure(client, created_user, name, arguments):
    response = _rpc(client, created_user.headers, name, arguments)
    assert response.status_code == 200 and response.json()["error"]["code"] == -32602
    with database.SessionLocal() as db:
        row = db.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
        )
        assert row.result is AuditResult.FAILED
        assert "secret-plain-value" not in (row.metadata_json or "")


@pytest.mark.parametrize("name", ["get_caller_tool_schema", "validate_caller_tool_arguments"])
def test_admin_cannot_send_another_users_caller_content_to_llm(
    client, created_user, created_key, admin_headers, name
):
    task_id = _make_waiting_task(created_key.id, created_user.user_id, tool_names=["lookup"])
    arguments = {"task_id": task_id, "tool_name": "lookup"}
    if name.startswith("validate"):
        arguments["arguments"] = {}
    response = _rpc(client, admin_headers, name, arguments)
    assert response.json()["result"]["isError"] is True
    assert "input_schema" not in response.text
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        assert task.response_payload_json is None
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
        )
        assert audit.result is AuditResult.DENIED


@pytest.mark.parametrize("failure", [False, True, "large"])
def test_shared_mcp_result_redaction_errors_and_size_limits(
    client, created_user, monkeypatch, failure
):
    secret = "sensitive-placeholder-short"
    tool = get_mcp_tool("list_models")

    def handler(*args):
        if failure is True:
            raise RuntimeError("internal/path " + secret)
        text = "x" * 40000 if failure == "large" else json.dumps({"nested": [{"password": secret}]})
        return {"content": [{"type": "text", "text": text}], "isError": False}

    monkeypatch.setattr(tool, "handler", handler)
    response = _rpc(client, created_user.headers, "list_models", {})
    assert secret not in response.text and "internal/path" not in response.text
    assert response.json()["result"]["isError"] is bool(failure)
    with database.SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
        )
        assert secret not in audit.metadata_json
        assert audit.result is (AuditResult.FAILED if failure else AuditResult.SUCCESS)


@pytest.mark.parametrize(
    "body,code",
    [
        ({"method": "ping", "id": 1}, -32600),
        ({"jsonrpc": "2.0", "method": "ping", "id": True}, -32600),
        ({"jsonrpc": "2.0", "method": "tools/call", "id": 1, "params": []}, -32602),
    ],
)
def test_rpc_envelope_errors_are_stable(client, created_user, body, code):
    response = client.post("/api/mcp/", headers=created_user.headers, json=body)
    assert response.json()["error"]["code"] == code


def test_notifications_have_no_jsonrpc_response(client, created_user):
    response = client.post(
        "/api/mcp/",
        headers=created_user.headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response.status_code == 202 and not response.content


def test_task_filter_accepts_current_completed_state(client, created_user):
    response = _rpc(client, created_user.headers, "list_tasks", {"state": "completed"})
    assert response.json()["result"]["isError"] is False


def test_recursive_context_redaction_and_route_removal():
    context, hits = build_page_context(
        route="/replies?token=shortsecret#password=x",
        feature="replies",
        resource={"task_id": "2"},
        context_version=1,
        unsaved_edit={
            "tool_calls": [
                {
                    "id": "c",
                    "name": "lookup",
                    "arguments": {
                        "items": [{"password": "shortsecret", "headers": {"X-Custom": "opaque"}}]
                    },
                }
            ]
        },
    )
    assert hits >= 3 and context["route"] == "/replies"
    assert "shortsecret" not in json.dumps(context) and "opaque" not in json.dumps(context)
    clean, _ = redact_value({"image": "data:image/png;base64,1234"})
    assert clean["image"] == "[ATTACHMENT-OMITTED]"
    clean, _ = redact_value({"password": {"type": "string", "value": "hidden"}})
    assert clean["password"] == "[REDACTED]"


@pytest.mark.parametrize("protocol", list(LLMProtocol))
def test_native_build_has_no_mutable_message_alias(protocol):
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    body = build_request(protocol, "model", messages, [])
    (body.get("input") or body["messages"])[-1]["content"] = "changed"
    assert messages[-1]["content"] == "u"


@pytest.mark.parametrize("protocol", [p.value for p in LLMProtocol])
@pytest.mark.parametrize("bad", ["[]", "null"])
def test_bad_tool_arguments_never_execute(client, created_user, protocol, bad):
    url = _setup(client, created_user, protocol)

    async def post(*args, **kwargs):
        return _reply(protocol, arguments=bad)

    with patch.object(AssistantService, "_post_upstream", post):
        response = client.post(
            url + "/messages", headers=created_user.headers, json={"text": "查询"}
        )
    assert response.status_code == 502
    assert len(client.get(url, headers=created_user.headers).json()["messages"]) == 1
    with database.SessionLocal() as db:
        assert not db.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
        )


@pytest.mark.parametrize("kind", ["empty", "reasoning", "duplicate"])
def test_invalid_round_cannot_persist_success(client, created_user, kind):
    url = _setup(client, created_user, "openai_chat")
    payload = _reply("openai_chat", calls=kind == "duplicate")
    message = payload["choices"][0]["message"]
    if kind == "duplicate":
        message["tool_calls"] *= 2
    else:
        message["content"] = ""
        if kind == "reasoning":
            message["reasoning_content"] = "未完成的内部推理"

    async def post(*args, **kwargs):
        return payload

    with patch.object(AssistantService, "_post_upstream", post):
        response = client.post(
            url + "/messages", headers=created_user.headers, json={"text": "查询"}
        )
    assert response.status_code == 502
    assert len(client.get(url, headers=created_user.headers).json()["messages"]) == 1


def test_total_budget_does_not_reset_between_rounds(client, created_user):
    url = _setup(client, created_user, "openai_chat")
    build = AssistantService._build_request

    async def short_budget(self, *args, **kwargs):
        protocol, base, secret, _timeout, body = await build(self, *args, **kwargs)
        return protocol, base, secret, 0.03, body

    async def post(*args, **kwargs):
        await asyncio.sleep(0.02)
        return _reply("openai_chat")

    with (
        patch.object(AssistantService, "_build_request", short_budget),
        patch.object(AssistantService, "_post_upstream", post),
    ):
        response = client.post(
            url + "/messages", headers=created_user.headers, json={"text": "查询"}
        )
    assert response.status_code == 504
    assert len(client.get(url, headers=created_user.headers).json()["messages"]) == 1


def test_owner_schema_retains_structure_and_validation_is_readonly(
    client, created_user, created_key
):
    task_id = _make_waiting_task(created_key.id, created_user.user_id, tool_names=["lookup"])
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        payload = json.loads(task.raw_payload_json)
        payload["tools"][0]["function"]["parameters"] = {
            "type": "object",
            "properties": {
                "password": {"type": "string", "default": "shortsecret", "value": "shortsecret"}
            },
            "required": ["password"],
        }
        task.raw_payload_json = json.dumps(payload)
        db.commit()
    result = _rpc(
        client,
        created_user.headers,
        "get_caller_tool_schema",
        {"task_id": task_id, "tool_name": "lookup"},
    ).json()["result"]
    detail = json.loads(result["content"][0]["text"])
    assert detail["tool"]["input_schema"]["properties"]["password"] == {"type": "string"}
    assert "shortsecret" not in json.dumps(result)
    response = _rpc(
        client,
        created_user.headers,
        "validate_caller_tool_arguments",
        {"task_id": task_id, "tool_name": "lookup", "arguments": {"password": "another-secret"}},
    )
    assert response.json()["result"]["isError"] is False
    assert "another-secret" not in response.text
    with database.SessionLocal() as db:
        assert not db.scalar(select(TaskDraft).where(TaskDraft.task_id == task_id))
        assert db.get(TaskInboxState, task_id) is None
        assert db.get(RequestTask, task_id).response_payload_json is None


def test_tool_error_result_is_returned_to_model_through_shared_entry(
    client, created_user, monkeypatch
):
    url = _setup(client, created_user, "openai_chat")
    captured = []
    tool = get_mcp_tool("list_models")

    def broken(*args):
        raise RuntimeError("private-path /unsafe fake-secret")

    async def post(self, protocol, base, secret, body, timeout, **kwargs):
        captured.append(deepcopy(body))
        return _reply("openai_chat", calls=len(captured) == 1)

    monkeypatch.setattr(tool, "handler", broken)
    with patch.object(AssistantService, "_post_upstream", post):
        response = client.post(
            url + "/messages", headers=created_user.headers, json={"text": "查询"}
        )
    assert response.status_code == 201
    result = json.loads(captured[1]["messages"][-1]["content"])
    assert result["isError"] is True and "fake-secret" not in json.dumps(captured)
    with database.SessionLocal() as db:
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.MCP_TOOL_CALLED.value)
        )
        assert audit.result is AuditResult.FAILED


@pytest.mark.parametrize("cap", [None, 10000])
def test_thinking_budget_preserves_explicit_limit(cap):
    body = {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    if cap:
        body["max_tokens"] = cap
    ensure_anthropic_budget(body)
    assert body["max_tokens"] == (cap or 6144)


@pytest.mark.parametrize(
    "extra",
    [
        {"max_tokens": 1024},
        {"tool_choice": {"type": "any"}},
        {"tool_choice": {"type": "tool", "name": "lookup"}},
    ],
)
def test_incompatible_thinking_options_fail_before_upstream(extra):
    body = {"thinking": {"type": "enabled", "budget_tokens": 4096}, **extra}
    with pytest.raises(DomainError):
        ensure_anthropic_budget(body)


@pytest.mark.parametrize(
    "raw",
    [
        '{"password": "hidden"}',
        "Cookie: hidden",
        "token=x",
        "-----BEGIN PRIVATE KEY-----\nhidden\n-----END PRIVATE KEY-----",
    ],
)
def test_free_text_credentials_include_short_and_quoted_values(raw):
    clean, hits = redact_text(raw)
    assert hits and "hidden" not in clean and "token=x" not in clean

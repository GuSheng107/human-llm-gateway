"""工作台小助手只读能力：真实 MCP HTTP、所有者隔离、脱敏、有界及无写入。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

import app.core.db as database
from app.domain.enums import AuditAction, InferenceProtocol
from app.protocols import chat_completions
from app.repositories.models import ApiKey, RequestTask, TaskDraft, User
from app.repositories.system import AppLogRepository, AuditRepository
from app.services.inference_service import InferenceService
from app.services.mcp.tools import get_mcp_tool


def _task(created_key, created_user, *, choice="auto", parallel=True, text="请查询天气"):
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": text}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": choice,
        "parallel_tool_calls": parallel,
    }
    raw = json.dumps(payload).encode()
    with database.SessionLocal() as db:
        task = InferenceService().create_task(
            db,
            key=db.get(ApiKey, created_key.id),
            owner=db.get(User, created_user.user_id),
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=chat_completions.parse_request(raw),
            raw_body=raw,
            headers={},
        )
        db.commit()
        return task.id


def _rpc(client, headers, name, args):
    response = client.post(
        "/api/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return result, json.loads(result["content"][0]["text"])


def _call(call_id="call_a", city="北京"):
    return {"id": call_id, "name": "weather", "arguments": {"city": city}}


def _handler(created_user, name, args):
    """工具自身的防御校验；RPC 参数错误契约由统一执行入口测试覆盖。"""
    with database.SessionLocal() as db:
        tool = get_mcp_tool(name)
        assert tool is not None
        result = tool.handler(db, db.get(User, created_user.user_id), args)
        return result, json.loads(result["content"][0]["text"])


def test_tools_registered_and_request_projection_redacted(client, created_user, created_key):
    task_id = _task(
        created_key, created_user, text="问题 sk-fake-secret-1234567890 https://host/path?token=abc"
    )
    listing = client.post(
        "/api/mcp/", headers=created_user.headers, json={"id": 1, "method": "tools/list"}
    ).json()
    names = {t["name"] for t in listing["result"]["tools"]}
    assert {"get_request_view", "validate_reply_draft", "get_trace_summary"} <= names
    result, data = _rpc(client, created_user.headers, "get_request_view", {"task_id": task_id})
    assert not result["isError"]
    assert data["task"]["id"] == str(task_id)
    assert data["caller_tools"]["definitions"][0]["name"] == "weather"
    assert data["current_input"]
    text = result["content"][0]["text"]
    assert "sk-fake-secret" not in text and "token=abc" not in text
    assert "raw_payload" not in text and "headers" not in data


@pytest.mark.parametrize("name", ["get_request_view", "validate_reply_draft"])
def test_workbench_owner_boundary_includes_admin(
    client, admin_headers, created_user, created_key, name
):
    task_id = _task(created_key, created_user)
    args = {"task_id": task_id}
    if name == "validate_reply_draft":
        args["draft"] = {"final_text": "hi"}
    foreign, foreign_data = _rpc(client, admin_headers, name, args)
    args["task_id"] = 999999
    missing, missing_data = _rpc(client, created_user.headers, name, args)
    assert foreign["isError"] and missing["isError"]
    assert foreign_data == missing_data


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_request_view", {"task_id": True}),
        ("get_request_view", {"task_id": "1"}),
        ("get_request_view", {"task_id": 0}),
        ("get_request_view", {"task_id": 1, "secret": "x"}),
        ("get_trace_summary", {"trace_id": ""}),
        ("get_trace_summary", {"trace_id": "x" * 65}),
        ("validate_reply_draft", {"task_id": 1, "draft": {"final_text": 1}}),
        ("validate_reply_draft", {"task_id": 1, "draft": {"unlisted": "x"}}),
        (
            "validate_reply_draft",
            {
                "task_id": 1,
                "draft": {"tool_calls": [{"id": "a", "name": "weather", "arguments": []}]},
            },
        ),
    ],
)
def test_bad_arguments_rejected(client, created_user, name, args):
    result, data = _handler(created_user, name, args)
    assert result["isError"] and data["error"] == "invalid_arguments"


@pytest.mark.parametrize(
    "choice,parallel,calls,valid",
    [
        ("auto", True, [], True),
        ("required", True, [], False),
        ("none", True, [_call()], False),
        ("required", True, [_call()], True),
        ("auto", False, [_call(), _call("call_b")], False),
        ("auto", True, [_call(), _call()], False),
        ("auto", True, [_call(city=13)], False),
        ("auto", True, [{"id": "a", "name": "other", "arguments": {}}], False),
        ({"type": "function", "function": {"name": "weather"}}, True, [], False),
    ],
)
def test_draft_final_policy_is_read_only(
    client, created_user, created_key, choice, parallel, calls, valid
):
    task_id = _task(created_key, created_user, choice=choice, parallel=parallel)
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        before = (task.state, task.version, task.response_payload_json, task.slot_released_at)
    result, data = _rpc(
        client,
        created_user.headers,
        "validate_reply_draft",
        {
            "task_id": task_id,
            "draft": {"final_text": "未保存的草稿", "tool_calls": calls},
        },
    )
    assert not result["isError"] and data["valid"] is valid
    assert "未保存的草稿" not in result["content"][0]["text"]
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        assert before == (
            task.state,
            task.version,
            task.response_payload_json,
            task.slot_released_at,
        )
        assert db.scalar(select(func.count()).select_from(TaskDraft)) == 0


def test_empty_draft_is_not_valid_final_reply(client, created_user, created_key):
    task_id = _task(created_key, created_user)
    _, data = _rpc(
        client, created_user.headers, "validate_reply_draft", {"task_id": task_id, "draft": {}}
    )
    assert data["valid"] is False and data["error"] == "empty_reply"


def test_request_output_is_bounded(client, created_user, created_key):
    task_id = _task(created_key, created_user, text="测试内容" * 3000)
    result, data = _rpc(client, created_user.headers, "get_request_view", {"task_id": task_id})
    assert len(result["content"][0]["text"].encode()) <= 32 * 1024
    assert data["truncated"]


def test_trace_only_returns_owned_metadata_and_is_bounded(client, admin_headers, created_user):
    with database.SessionLocal() as db:
        repo = AppLogRepository()
        for n in range(25):
            repo.add(
                db,
                user_id=created_user.user_id,
                request_id="trace-test",
                event=f"llm.event{n}",
                message="private-body",
                context={"secret": "not-for-assistant"},
            )
        repo.add(db, user_id=1, request_id="trace-test", event="foreign.secret", message="other")
        AuditRepository().add(
            db,
            action=AuditAction.MCP_TOOL_CALLED,
            resource_type="mcp_tool",
            actor_user_id=created_user.user_id,
            request_id="trace-test",
            metadata={"payload": "private-audit-body"},
        )
        db.commit()
    result, data = _rpc(
        client, created_user.headers, "get_trace_summary", {"trace_id": "trace-test"}
    )
    assert not result["isError"] and len(data["events"]) == 20 and data["has_more"]
    text = result["content"][0]["text"]
    assert not any(
        s in text
        for s in ("private-body", "not-for-assistant", "foreign.secret", "private-audit-body")
    )
    foreign, _ = _rpc(client, admin_headers, "get_trace_summary", {"trace_id": "absent-trace"})
    assert foreign["isError"]


@pytest.mark.parametrize("kind", ["bytes", "depth", "nodes"])
def test_large_or_deep_arguments_rejected_before_schema(client, created_user, kind):
    arguments = {"data": "x" * 70000}
    expected = "arguments_too_large"
    if kind == "depth":
        arguments = {}
        for _ in range(20):
            arguments = {"child": arguments}
        expected = "arguments_too_complex"
    if kind == "nodes":
        arguments = {"many": list(range(10001))}
        expected = "arguments_too_complex"
    result, data = _handler(
        created_user,
        "validate_reply_draft",
        {
            "task_id": 1,
            "draft": {"tool_calls": [{"id": "a", "name": "x", "arguments": arguments}]},
        },
    )
    assert result["isError"] and data["error"] == expected


def test_schema_failure_has_safe_path_without_echoing_bad_value(client, created_user, created_key):
    task_id = _task(created_key, created_user)
    _, data = _rpc(
        client,
        created_user.headers,
        "validate_reply_draft",
        {
            "task_id": task_id,
            "draft": {"tool_calls": [_call(city={"private": "private-argument-value"})]},
        },
    )
    assert not data["valid"] and data["error_path"] == "/city"
    assert "Schema" in data["message"]
    assert "private-argument-value" not in json.dumps(data)


def test_request_tool_schema_nested_secrets_and_media_omitted(client, created_user, created_key):
    task_id = _task(created_key, created_user)
    with database.SessionLocal() as db:
        task = db.get(RequestTask, task_id)
        raw = json.loads(task.raw_payload_json)
        raw["tools"][0]["function"]["parameters"]["default"] = {
            "nested": {"password": "fictional-password", "token": "fictional-token"}
        }
        task.raw_payload_json = json.dumps(raw)
        normalized = json.loads(task.normalized_request_json)
        normalized["context"][0]["content"] = [
            {"type": "text", "text": "hello"},
            {
                "type": "image_url",
                "image_url": {"url": "https://images.test/a?token=private-image-token"},
            },
        ]
        normalized["messages"] = normalized["context"]
        task.normalized_request_json = json.dumps(normalized)
        db.commit()
    result, data = _rpc(client, created_user.headers, "get_request_view", {"task_id": task_id})
    assert data["attachment_count"] == 1
    text = result["content"][0]["text"]
    assert not any(
        s in text
        for s in ("fictional-password", "fictional-token", "private-image-token", "images.test")
    )

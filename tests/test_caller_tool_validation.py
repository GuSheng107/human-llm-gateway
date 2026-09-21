"""Caller Tool 阶段规则：可编辑草稿与最终回复不能混用校验强度。"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.core.db as database
from app.domain.caller_tools import CallerToolChoice, build_caller_tool_catalog
from app.domain.enums import InferenceProtocol, TaskState
from app.domain.errors import DomainError, DomainErrorCode
from app.domain.values import ReplyDraft, ReplyToolCall
from app.repositories.models import RequestTask, User
from app.services.caller_tool_service import validate_full, validate_structural
from app.services.task_service import TaskService
from tests.test_im_commands import _inbound, webhook_scene  # noqa: F401
from tests.test_m6_tasks import _make_waiting_task
from tests.test_m7_llm_forward import (
    _bearer,
    _create_llm_config,
    _create_strategy_key,
    _latest_task,
    _llm_body,
)


def _payload(protocol, choice, parallel=True):
    schema = {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}}
    functions = [{"name": name, "parameters": schema} for name in ("search", "calc")]
    if protocol is InferenceProtocol.OPENAI_CHAT:
        return {
            "tools": [{"type": "function", "function": fn} for fn in functions],
            "tool_choice": {"type": "function", "function": {"name": "search"}}
            if choice == "named"
            else choice,
            "parallel_tool_calls": parallel,
        }
    if protocol is InferenceProtocol.OPENAI_RESPONSES:
        return {
            "tools": [{"type": "function", **fn} for fn in functions],
            "tool_choice": {"type": "function", "name": "search"} if choice == "named" else choice,
            "parallel_tool_calls": parallel,
        }
    return {
        "tools": [{"name": fn["name"], "input_schema": schema} for fn in functions],
        "tool_choice": {
            "type": {"required": "any", "named": "tool"}.get(choice, choice),
            **({"name": "search"} if choice == "named" else {}),
            "disable_parallel_tool_use": not parallel,
        },
    }


def _calls(names):
    return [
        {"id": f"call_{i}", "name": name, "arguments": {"q": "x"}} for i, name in enumerate(names)
    ]


@pytest.mark.parametrize("protocol", list(InferenceProtocol))
@pytest.mark.parametrize("as_models", [False, True])
@pytest.mark.parametrize(
    "choice,names,parallel,valid",
    [
        ("auto", [], True, True),
        ("required", [], True, False),
        ("none", ["search"], True, False),
        ("none", [], True, True),
        ("named", ["search"], True, True),
        ("named", ["calc"], True, False),
        ("named", ["search", "calc"], True, False),
        ("named", ["search", "search"], True, True),
        ("required", ["search", "calc"], False, False),
        ("required", ["search"], False, True),
    ],
)
def test_final_policy_all_protocols(protocol, as_models, choice, names, parallel, valid):
    catalog = build_caller_tool_catalog(protocol, _payload(protocol, choice, parallel))
    calls = _calls(names)
    if as_models:
        calls = [ReplyToolCall(**call) for call in calls]
    validate_structural(catalog, calls)  # 每一种结构有效的候选都允许编辑。
    if valid:
        validate_full(catalog, calls)
    else:
        with pytest.raises(DomainError):
            validate_full(catalog, calls)


@pytest.mark.parametrize(
    "calls",
    [
        [{"id": " ", "name": "search", "arguments": {"q": "x"}}],
        _calls(["missing"]),
        [{"id": "call_1", "name": "search", "arguments": {}}],
        [{"id": "call_1", "name": "search", "arguments": {"q": 12}}],
        [{"id": "call_1", "name": "search", "arguments": []}],
        _calls(["search"]) * 2,
    ],
)
def test_structural_failures_are_rejected_in_both_stages(calls):
    catalog = build_caller_tool_catalog(
        InferenceProtocol.OPENAI_CHAT, _payload(InferenceProtocol.OPENAI_CHAT, "auto")
    )
    for validator in (validate_structural, validate_full):
        with pytest.raises(DomainError):
            validator(catalog, calls)


@pytest.mark.parametrize("protocol", list(InferenceProtocol))
def test_task_submission_cannot_bypass_required_with_empty_calls(protocol):
    task = SimpleNamespace(
        protocol=protocol, raw_payload_json=json.dumps(_payload(protocol, "required"))
    )
    draft = ReplyDraft(final_text="仍在编辑")
    assert TaskService.validate_reply_draft(task, draft, strict=False) is draft
    with pytest.raises(DomainError):
        TaskService.validate_reply_draft(task, draft, strict=True)


def test_manual_required_failure_preserves_draft_and_waiting_task(
    client, created_user, created_key
):
    task_id = _make_waiting_task(created_key.id, created_user.user_id, tool_names=["search"])
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        raw = json.loads(task.raw_payload_json)
        raw["tool_choice"] = "required"
        task.raw_payload_json = json.dumps(raw)
        session.commit()
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json={"final_text": "待补工具"},
    )
    assert saved.status_code == 201
    reply = client.post(
        f"/api/tasks/{task_id}/reply", headers=created_user.headers, json={"final_text": "待补工具"}
    )
    assert reply.status_code == 400
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        assert task.state is TaskState.WAITING_HUMAN
        assert task.response_payload_json is None
    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["drafts"][0]["final_text"] == "待补工具"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "choice,names,parallel",
    [
        ("required", [], True),
        ("none", ["search"], True),
        ("named", ["search", "calc"], True),
        ("auto", ["search", "calc"], False),
    ],
)
def test_invalid_auto_result_not_persisted_and_slot_released(
    client, created_user, stream, choice, names, parallel
):
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client, created_user.headers, strategy="llm", llm_config_id=int(cfg["id"])
    )
    from app.services.llm_upstream import UpstreamChunk

    async def nonstream(*args, **kwargs):
        return ReplyDraft(final_text="candidate", tool_calls=_calls(names))

    async def streaming(*args, **kwargs):
        return [UpstreamChunk(text="candidate")] + [
            UpstreamChunk(
                tool_call={
                    "index": i,
                    "id": call["id"],
                    "name": call["name"],
                    "arguments_delta": json.dumps(call["arguments"]),
                }
            )
            for i, call in enumerate(_calls(names))
        ]

    with (
        patch(
            "app.services.llm_forward_service.LlmForwardService._call_upstream",
            side_effect=nonstream,
        ),
        patch(
            "app.services.llm_forward_service.LlmForwardService._call_upstream_stream",
            side_effect=streaming,
        ),
    ):
        response = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": stream,
                **_payload(InferenceProtocol.OPENAI_CHAT, choice, parallel),
            },
        )
    assert response.status_code == 500, response.text
    task = _latest_task(int(key["id"]))
    assert task.state is TaskState.FAILED
    assert task.response_payload_json is None
    with database.SessionLocal() as session:
        assert session.get(User, created_user.user_id).active_task_count == 0


def test_stream_aggregation_failure_releases_slot(client, created_user):
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client, created_user.headers, strategy="llm", llm_config_id=int(cfg["id"])
    )
    with (
        patch(
            "app.services.llm_forward_service.LlmForwardService._call_upstream_stream",
            return_value=[],
        ),
        patch(
            "app.services.llm_upstream.finalize_collected",
            side_effect=DomainError(DomainErrorCode.UPSTREAM_ERROR, "损坏的工具参数"),
        ),
    ):
        response = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    assert response.status_code >= 400
    task = _latest_task(int(key["id"]))
    assert task.state is TaskState.FAILED
    assert task.response_payload_json is None
    with database.SessionLocal() as session:
        assert session.get(User, created_user.user_id).active_task_count == 0


@pytest.mark.parametrize("via_draft", [False, True])
def test_im_final_submission_obeys_required(client, request, via_draft):
    scene = request.getfixturevalue("webhook_scene")
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        raw = json.loads(task.raw_payload_json)
        raw.update(_payload(InferenceProtocol.OPENAI_CHAT, "required"))
        task.raw_payload_json = json.dumps(raw)
        session.commit()
    if via_draft:
        assert _inbound(client, scene, "stage", "/ans 待补工具").json()["result"] == "accepted"
    result = _inbound(client, scene, "submit", "/commit" if via_draft else "纯文本")
    assert result.json()["result"] == "unhandled"
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.WAITING_HUMAN
        assert task.response_payload_json is None


@pytest.mark.parametrize(
    "raw_choice,expected,expected_name",
    [
        ({"type": "Tool", "name": "search"}, CallerToolChoice.NAMED, "search"),
        ({"type": "Any"}, CallerToolChoice.REQUIRED, None),
        ({"type": "NONE"}, CallerToolChoice.NONE, None),
        ({"type": "Auto"}, CallerToolChoice.AUTO, None),
        ("any", CallerToolChoice.REQUIRED, None),
    ],
)
def test_anthropic_tool_choice_type_is_case_insensitive(raw_choice, expected, expected_name):
    """Anthropic 的 tool_choice.type 大小写不敏感，大写不得静默降级成 auto。"""
    catalog = build_caller_tool_catalog(
        InferenceProtocol.ANTHROPIC_MESSAGES,
        {
            "tools": [{"name": "search", "input_schema": {"type": "object"}}],
            "tool_choice": raw_choice,
        },
    )
    assert catalog.policy.choice is expected
    assert catalog.policy.required_name == expected_name


@pytest.mark.parametrize("choice", ["any", "Any", {"type": "ANY"}])
def test_uppercase_required_still_rejects_empty_reply(choice):
    """大写写法同样触发 required 约束：空 Tool Call 列表必须被拒。"""
    catalog = build_caller_tool_catalog(
        InferenceProtocol.ANTHROPIC_MESSAGES,
        {
            "tools": [{"name": "search", "input_schema": {"type": "object"}}],
            "tool_choice": choice,
        },
    )
    with pytest.raises(DomainError):
        validate_full(catalog, [])

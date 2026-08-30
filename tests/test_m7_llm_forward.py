"""M7-C LLM 自动转发测试（llm / human_fallback_llm 策略）。

覆盖：
- llm 策略：任务创建后直接转发，不经人工等待，响应使用 Fake Model 标识。
- human_fallback_llm：人工超时后触发一次 fallback；人工先到则不触发。
- fallback 转发失败（上游错误/声明丢失）走终态，不重试。
- 身份 system 指令：description 派生 + 兜底；追加在调用方 system 之后。
- 跨协议转发按矩阵返回 unsupported（对外通用 500，不暴露细节）。
- 上游 tool call 只写入 ReplyDraft，不被执行。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import app.core.db as database
from app.domain.enums import InferenceProtocol, TaskState
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, FakeModel, RequestTask, User
from app.services.inference_service import InferenceService
from app.services.llm_forward_service import (
    LlmForwardService,
    _inject_identity_anthropic,
    _inject_identity_chat,
    identity_system_message,
)

_UPSTREAM_CHAT = "app.services.llm_upstream.post_chat_completions"
_UPSTREAM_ANTHROPIC = "app.services.llm_upstream.post_anthropic_messages"


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _llm_body(
    *,
    name: str = "primary",
    protocol: str = "openai_compatible",
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "protocol": protocol,
        "base_url": (
            "https://api.example.com/v1"
            if protocol == "openai_compatible"
            else "https://api.anthropic.com"
        ),
        "api_key": "sk-llm-test",
        "model": "gpt-4o-mini" if protocol == "openai_compatible" else "claude-3-5-sonnet",
        "timeout_seconds": 60,
        "enabled": enabled,
    }


def _create_llm_config(client, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/api/llm-configs", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_strategy_key(
    client,
    headers: dict[str, str],
    *,
    strategy: str,
    llm_config_id: int | None,
) -> dict[str, Any]:
    resp = client.post(
        "/api/api-keys",
        headers=headers,
        json={
            "name": f"key-{strategy}",
            "delivery_mode": "web",
            "reply_strategy": strategy,
            "llm_config_id": llm_config_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _chat_upstream_ok(**kwargs: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fwd",
        "model": kwargs.get("request_body", {}).get("model", "gpt-4o-mini"),
        "choices": [
            {
                "message": {"role": "assistant", "content": "上游转发回答"},
                "finish_reason": "stop",
            }
        ],
    }


def _bearer(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def _latest_task(api_key_id: int) -> RequestTask:
    from sqlalchemy import select

    with database.SessionLocal() as session:
        return (
            session.execute(
                select(RequestTask)
                .where(RequestTask.api_key_id == api_key_id)
                .order_by(RequestTask.id.desc())
            )
            .scalars()
            .first()
        )


# ----------------------------------------------------------------------
# 身份 system 指令
# ----------------------------------------------------------------------


def test_identity_message_from_description() -> None:
    model = FakeModel(
        id=1,
        scope=None,
        owner_user_id=None,
        model_id="deepseek-v4-pro",
        display_name="网关模型",
        owned_by="gateway",
        description="你是专业翻译助手。",
    )
    message = identity_system_message(model, "deepseek-v4-pro")
    assert message.startswith("[deepseek-v4-pro]")
    assert "你是专业翻译助手。" in message


def test_identity_message_fallback_without_description() -> None:
    model = FakeModel(
        id=1,
        scope=None,
        owner_user_id=None,
        model_id="deepseek-v4-pro",
        display_name=None,
        owned_by="gateway",
        description=None,
    )
    message = identity_system_message(model, "deepseek-v4-pro")
    assert message.startswith("[deepseek-v4-pro]")
    assert "helpful assistant" in message


def test_identity_message_none_model() -> None:
    message = identity_system_message(None, "deepseek-v4-pro")
    assert message.startswith("[deepseek-v4-pro]")


def test_inject_identity_chat_appends_to_existing_system() -> None:
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "原有指令"},
            {"role": "user", "content": "hi"},
        ],
    }
    result = _inject_identity_chat(body, "[fake] identity")
    assert result["messages"][0]["role"] == "system"
    assert "原有指令" in result["messages"][0]["content"]
    assert "[fake] identity" in result["messages"][0]["content"]
    assert result["messages"][1] == {"role": "user", "content": "hi"}


def test_inject_identity_chat_prepends_when_no_system() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    result = _inject_identity_chat(body, "[fake] identity")
    assert result["messages"][0] == {"role": "system", "content": "[fake] identity"}


def test_inject_identity_anthropic_string_system() -> None:
    result = _inject_identity_anthropic({"system": "原有"}, "[fake] identity")
    assert "原有" in result["system"]
    assert "[fake] identity" in result["system"]


def test_inject_identity_anthropic_block_system() -> None:
    blocks = [{"type": "text", "text": "b1"}]
    result = _inject_identity_anthropic({"system": blocks}, "[fake] identity")
    assert result["system"][0] == blocks[0]
    assert result["system"][1] == {"type": "text", "text": "[fake] identity"}


def test_inject_identity_anthropic_no_system() -> None:
    result = _inject_identity_anthropic({}, "[fake] identity")
    assert result["system"] == "[fake] identity"


# ----------------------------------------------------------------------
# llm 策略：直接转发
# ----------------------------------------------------------------------


def test_llm_strategy_direct_forward_non_stream(client, created_user) -> None:
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        messages = kwargs["request_body"]["messages"]
        assert any(m["role"] == "system" and "deepseek-v4-pro" in m["content"] for m in messages)
        return _chat_upstream_ok(**kwargs)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "你好"}],
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "deepseek-v4-pro"
    assert body["choices"][0]["message"]["content"] == "上游转发回答"


def test_llm_strategy_direct_forward_stream(client, created_user) -> None:
    """stream=true 时上游走 SSE 流式接收，聚合后伪流式输出（§13.3）。"""
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    from app.services.llm_upstream import UpstreamChunk

    async def fake_stream(**kwargs: Any):
        # stream=true 由 stream_chat_completions 内部注入（body 收到时未带）。
        for chunk in (
            UpstreamChunk(text="流式"),
            UpstreamChunk(text="回答"),
        ):
            yield chunk

    with patch("app.services.llm_upstream.stream_chat_completions", side_effect=fake_stream):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "data:" in resp.text
    assert "流式回答" in resp.text
    assert "[DONE]" in resp.text
    row = _latest_task(int(key["id"]))
    assert row.state is TaskState.COMPLETED


def test_llm_strategy_upstream_failure_returns_500(client, created_user) -> None:
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    from app.domain.errors import DomainError, DomainErrorCode

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游 LLM 返回 500", status_code=502)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 500
    assert "fallback" not in resp.text.lower()
    row = _latest_task(int(key["id"]))
    assert row.state is TaskState.FAILED
    assert row.slot_released_at is not None


def test_llm_strategy_task_state_transitions(client, created_user) -> None:
    """llm 策略任务最终 COMPLETED。"""
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        return _chat_upstream_ok(**kwargs)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    row = _latest_task(int(key["id"]))
    assert row.state is TaskState.COMPLETED
    assert row.response_payload_json is not None


# ----------------------------------------------------------------------
# human_fallback_llm：超时触发一次
# ----------------------------------------------------------------------


def _make_task(api_key_id: int, user_id: int) -> int:
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        key_row = session.get(ApiKey, api_key_id)
        owner = session.get(User, user_id)
        task = InferenceService().create_task(
            session,
            key=key_row,
            owner=owner,
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        return task.id


def test_fallback_triggers_once_after_human_timeout(client, created_user) -> None:
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="human_fallback_llm",
        llm_config_id=int(cfg["id"]),
    )
    task_id = _make_task(int(key["id"]), created_user.user_id)

    call_count = {"n": 0}

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return _chat_upstream_ok(**kwargs)

    def run_forward() -> Any:
        with database.SessionLocal() as session:
            task_row = session.get(RequestTask, task_id)
            service = LlmForwardService()
            return asyncio.run(service.forward(session, task_row, reason="human_timeout"))

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        accepted, draft, error = run_forward()
        assert accepted is True, error
        assert draft is not None
        assert draft.final_text == "上游转发回答"
        assert call_count["n"] == 1

        # 第二次 forward 声明丢失（已 FORWARDING_LLM / RESPONSE_READY）
        accepted2, _draft2, error2 = run_forward()
        assert accepted2 is False
        assert error2 == "claim_lost"
        assert call_count["n"] == 1


def test_fallback_not_triggered_when_human_replies_first(client, created_user) -> None:
    """人工先到：任务 RESPONSE_READY，fallback 声明失败，上游从未被调用。"""
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="human_fallback_llm",
        llm_config_id=int(cfg["id"]),
    )
    task_id = _make_task(int(key["id"]), created_user.user_id)

    submit = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={"final_text": "人工先到"},
    )
    assert submit.status_code == 201

    call_count = {"n": 0}

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return _chat_upstream_ok(**kwargs)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post), database.SessionLocal() as session:
        task_row = session.get(RequestTask, task_id)
        service = LlmForwardService()
        accepted, _draft, error = asyncio.run(
            service.forward(session, task_row, reason="human_timeout")
        )
    assert accepted is False
    assert error == "claim_lost"
    assert call_count["n"] == 0

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["result_draft"]["final_text"] == "人工先到"


def test_fallback_upstream_failure_keeps_timeout_terminal(client, created_user) -> None:
    """fallback 上游失败：不重试，调用方按终态处理。"""
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="human_fallback_llm",
        llm_config_id=int(cfg["id"]),
    )
    task_id = _make_task(int(key["id"]), created_user.user_id)

    from app.domain.errors import DomainError, DomainErrorCode

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        raise DomainError(DomainErrorCode.UPSTREAM_ERROR, "上游 LLM 返回 500", status_code=502)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post), database.SessionLocal() as session:
        task_row = session.get(RequestTask, task_id)
        service = LlmForwardService()
        accepted, _draft, error = asyncio.run(
            service.forward(session, task_row, reason="human_timeout")
        )
    assert accepted is False
    assert error == "upstream_error"


# ----------------------------------------------------------------------
# 跨协议转发
# ----------------------------------------------------------------------


def test_cross_protocol_forward_chat_to_anthropic(client, created_user) -> None:
    """M7-D：Chat 任务 + Anthropic LLM 跨协议转发经 cross 矩阵转换成功。"""
    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(name="anthro-cross", protocol="anthropic"),
    )
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        body = kwargs["request_body"]
        # cross 矩阵转换出的 Anthropic 请求体
        assert body["model"] == "claude-3-5-sonnet"
        assert any(m == {"role": "user", "content": "hi"} for m in body["messages"])
        return {
            "id": "msg_cross",
            "content": [{"type": "text", "text": "跨协议转发回答"}],
        }

    with patch(_UPSTREAM_ANTHROPIC, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200, resp.text
    # 响应仍是调用方协议（Chat），model 为 Fake Model
    body = resp.json()
    assert body["model"] == "deepseek-v4-pro"
    assert body["choices"][0]["message"]["content"] == "跨协议转发回答"
    row = _latest_task(int(key["id"]))
    assert row.state is TaskState.COMPLETED


def test_cross_protocol_forward_rejects_unequivalent_field(client, created_user) -> None:
    """跨协议不可等价字段（response_format 转 Anthropic）整请求拒绝。"""
    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(name="anthro-strict", protocol="anthropic"),
    )
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("不可等价字段不应调用上游")

    with patch(_UPSTREAM_ANTHROPIC, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "json_object"},
            },
        )
    # 上游不可达 -> 转发失败 -> 对外通用 500（不暴露 matrix 细节）
    assert resp.status_code == 500
    row = _latest_task(int(key["id"]))
    assert row.state is TaskState.FAILED


def test_inference_to_llm_protocol_mapping() -> None:
    """协议映射表：Chat/Responses -> openai_compatible；Anthropic -> anthropic。"""
    from app.services.llm_forward_service import _INFERENCE_TO_LLM

    assert _INFERENCE_TO_LLM[InferenceProtocol.OPENAI_CHAT].value == "openai_compatible"
    assert _INFERENCE_TO_LLM[InferenceProtocol.OPENAI_RESPONSES].value == "openai_compatible"
    assert _INFERENCE_TO_LLM[InferenceProtocol.ANTHROPIC_MESSAGES].value == "anthropic"


# ----------------------------------------------------------------------
# 上游 tool call 只写入草稿
# ----------------------------------------------------------------------


def test_forward_upstream_tool_calls_recorded_not_executed(client, created_user) -> None:
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_fwd_1",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"q": "x"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        resp = client.post(
            "/v1/chat/completions",
            headers=_bearer(key["plaintext"]),
            json={
                "model": "deepseek-v4-pro",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["tool_calls"]
    assert body["choices"][0]["finish_reason"] == "tool_calls"


def test_forward_missing_config_snapshot_fails(client, created_user) -> None:
    """快照缺失（理论不可达）：转发失败返回 upstream_error。"""
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    key = _create_strategy_key(
        client,
        created_user.headers,
        strategy="llm",
        llm_config_id=int(cfg["id"]),
    )
    task_id = _make_task(int(key["id"]), created_user.user_id)
    from sqlalchemy import update as sa_update

    with database.SessionLocal() as session:
        session.execute(
            sa_update(RequestTask)
            .where(RequestTask.id == task_id)
            .values(llm_config_id_snapshot=None)
        )
        session.commit()

        service = LlmForwardService()
        accepted, _draft, error = asyncio.run(
            service.forward(session, session.get(RequestTask, task_id), reason="direct")
        )
    assert accepted is False
    assert error == "upstream_error"

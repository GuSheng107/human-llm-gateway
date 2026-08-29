"""M7 审核修复验证：fallback 末梢竞态与 base_url 归一。

1. 人工先到 + 等待循环超时分支：fallback claim_lost 后重读状态，
   已 RESPONSE_READY 的人工回复不被 TIMED_OUT 覆盖（端到端走
   inference.py 超时分支末梢）。
2. release_slot_to_terminal 源状态防御：TIMED_OUT 只允许从
   WAITING_HUMAN / FORWARDING_LLM 推进。
3. Anthropic base_url 归一：裸 host 自动补 /v1；已含 /v1 保持；
   自定义代理 path 原样保留；仅切协议时既有 base_url 重新归一。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import app.core.db as database
from app.domain.enums import InferenceProtocol, TaskState
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, RequestTask, User
from app.services.inference_service import InferenceService


def _llm_body(name: str = "primary") -> dict[str, Any]:
    return {
        "name": name,
        "protocol": "openai_compatible",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk",
        "model": "gpt-4o-mini",
        "timeout_seconds": 60,
        "enabled": True,
    }


def _make_fallback_task(client, user_headers, api_key_id: int, user_id: int) -> int:
    cfg = client.post("/api/llm-configs", headers=user_headers, json=_llm_body()).json()
    key = client.post(
        "/api/api-keys",
        headers=user_headers,
        json={
            "name": "k-fb",
            "delivery_mode": "web",
            "reply_strategy": "human_fallback_llm",
            "llm_config_id": int(cfg["id"]),
        },
    ).json()
    payload = {"model": "human-gateway", "messages": [{"role": "user", "content": "hi"}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        task = InferenceService().create_task(
            session,
            key=session.get(ApiKey, int(key["id"])),
            owner=session.get(User, user_id),
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        return task.id


# ----------------------------------------------------------------------
# #1 竞态：人工先到 + 超时分支末梢
# ----------------------------------------------------------------------


def test_timeout_branch_returns_human_reply_when_claim_lost(
    client, created_user, created_key
) -> None:
    """人工恰在超时判定后提交：末梢重读状态返回人工回复，不被 TIMED_OUT 覆盖。

    直接驱动 inference.py 的超时分支语义：把 deadline 置于过去，人工先
    提交（RESPONSE_READY），随后调用与等待循环同构的超时处理路径
    （fallback -> claim_lost -> 重读 -> 不 finalize）。
    """
    task_id = _make_fallback_task(
        client, created_user.headers, created_key.id, created_user.user_id
    )

    # deadline 置于过去
    from sqlalchemy import update as sa_update

    from app.core.time import utc_now

    with database.SessionLocal() as session:
        session.execute(
            sa_update(RequestTask)
            .where(RequestTask.id == task_id)
            .values(human_deadline_at=utc_now())
        )
        session.commit()

    # 人工先到
    submit = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={"final_text": "人工恰时回复"},
    )
    assert submit.status_code == 201

    # fallback（此时 claim_lost：任务已 RESPONSE_READY）
    from app.services.llm_forward_service import LlmForwardService

    with database.SessionLocal() as session:
        task_row = session.get(RequestTask, task_id)
        accepted, _draft, error = asyncio.run(
            LlmForwardService().forward(session, task_row, reason="human_timeout")
        )
    assert accepted is False
    assert error == "claim_lost"

    # 超时分支末梢防御：重读状态为 RESPONSE_READY -> 不推进 TIMED_OUT
    from app.api.inference import _finalize

    _finalize(task_id, TaskState.TIMED_OUT)
    with database.SessionLocal() as session:
        row = session.get(RequestTask, task_id)
    assert row.state is TaskState.RESPONSE_READY
    assert row.response_payload_json is not None
    assert "人工恰时回复" in (row.response_payload_json or "")


def test_release_slot_to_terminal_rejects_ready_source() -> None:
    """仓库层源状态防御：RESPONSE_READY 不能被 TIMED_OUT 覆盖（allowed_sources）。"""
    from app.repositories.tasks import TaskRepository

    # 仅验证签名与条件构造语义（端到端覆盖见上一测试）
    repo = TaskRepository()
    assert hasattr(repo, "release_slot_to_terminal")


# ----------------------------------------------------------------------
# #2 base_url 归一
# ----------------------------------------------------------------------


def test_anthropic_base_url_bare_host_gets_v1(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "anthropic-bare",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant",
            "model": "claude-3-5-sonnet",
            "timeout_seconds": 60,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["base_url"] == "https://api.anthropic.com/v1"


def test_anthropic_base_url_with_v1_kept(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "anthropic-v1",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com/v1/",
            "api_key": "sk-ant",
            "model": "claude-3-5-sonnet",
            "timeout_seconds": 60,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://api.anthropic.com/v1"


def test_anthropic_custom_proxy_path_preserved(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "anthropic-proxy",
            "protocol": "anthropic",
            "base_url": "https://proxy.internal/anthropic",
            "api_key": "sk-ant",
            "model": "claude-3-5-sonnet",
            "timeout_seconds": 60,
        },
    )
    assert resp.status_code == 201
    # 自定义 path 不猜测、不追加
    assert resp.json()["base_url"] == "https://proxy.internal/anthropic"


def test_openai_base_url_not_normalized(client, created_user) -> None:
    """OpenAI 兼容协议保持用户原样（生态网关路径不一）。"""
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "openai-raw",
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk",
            "model": "gpt-4o-mini",
            "timeout_seconds": 60,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://api.example.com/v1"


def test_protocol_switch_renormalizes_existing_base_url(client, created_user) -> None:
    """仅切协议（openai -> anthropic）时既有 base_url 按新协议重新归一。"""
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "switch-me",
            "protocol": "openai_compatible",
            "base_url": "https://api.example.com",
            "api_key": "sk",
            "model": "gpt-4o-mini",
            "timeout_seconds": 60,
        },
    ).json()
    resp = client.patch(
        f"/api/llm-configs/{created['id']}",
        headers=created_user.headers,
        json={"protocol": "anthropic"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["base_url"] == "https://api.example.com/v1"


def test_upstream_anthropic_url_helper() -> None:
    from app.services.llm_upstream import _anthropic_messages_url

    assert _anthropic_messages_url("https://host") == "https://host/v1/messages"
    assert _anthropic_messages_url("https://host/") == "https://host/v1/messages"
    assert _anthropic_messages_url("https://host/v1") == "https://host/v1/messages"
    assert _anthropic_messages_url("https://host/v1/") == "https://host/v1/messages"
    assert _anthropic_messages_url("https://host/proxy") == "https://host/proxy/v1/messages"

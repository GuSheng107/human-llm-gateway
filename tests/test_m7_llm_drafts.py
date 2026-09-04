"""M7-B 任务 LLM 草稿生成测试（docs/API_CONTRACT.md §9）。

覆盖：
- 同协议 Chat + OpenAI 兼容 LLM 成功生成草稿
- 同协议 Anthropic + Anthropic LLM 成功生成草稿
- 跨协议（Chat + Anthropic）返回 400
- LLM 配置不属于自己 / 已停用 / 缺失 → 4xx
- 任务已结束 / 非归属 → 4xx
- 生成的草稿可在编辑器中继续编辑并提交回复
- 上游超时 / 错误返回 504/502（不暴露 Secret）
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _llm_body(
    *,
    name: str = "primary",
    protocol: str = "openai_chat",
    base_url: str = "https://api.example.com/v1",
    api_key: str = "sk-llm-test",
    model: str = "gpt-4o-mini",
    timeout_seconds: int = 60,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "enabled": enabled,
    }


def _create_llm_config(client, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/api/llm-configs", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_waiting_task(client, key_id: int, user_id: int, *, content: str = "hello") -> int:
    """直接经编排服务创建一个 WAITING_HUMAN 任务（与 test_m6_tasks 同样的方式）。"""
    import app.core.db as database
    from app.domain.enums import InferenceProtocol
    from app.protocols import chat_completions as chat_protocol
    from app.repositories.models import ApiKey, User
    from app.services.inference_service import InferenceService

    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": content}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, user_id)
        task = InferenceService().create_task(
            session,
            key=key,
            owner=owner,
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        assert task.id is not None
        return task.id


# ----------------------------------------------------------------------
# 同协议 OpenAI 生成
# ----------------------------------------------------------------------


def test_generate_chat_with_openai_chat_llm(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(
        client, created_key.id, created_user.user_id, content="hello world"
    )
    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(),
    )

    async def fake(**kwargs: Any) -> Any:
        return {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "上游生成的回答",
                        "reasoning_content": "推理内容",
                        "tool_calls": [
                            {
                                "id": "call_001",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"q": "weather"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "llm"
    assert body["state"] == "editing"
    assert body["reasoning"] == "推理内容"
    assert body["final_text"] == "上游生成的回答"
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "search"
    assert body["tool_calls"][0]["arguments"] == {"q": "weather"}


def test_generate_draft_uses_active_draft_slot(client, created_user, created_key) -> None:
    """生成后 active_draft_id 指向 LLM 草稿。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    async def fake(**kwargs: Any) -> Any:
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}},
            ]
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 201
    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["active_draft_id"] == resp.json()["id"]


def test_generate_rejects_duplicate_llm_draft(client, created_user, created_key) -> None:
    """已存在未提交 LLM 草稿时重复生成 == 合并更新同一草稿（不再 409）。

    三模式生成需要「先生成思考链、再按它生成回复」的序列，因此同任务
    只允许一条 LLM 编辑态草稿，重复生成按模式合并并递增 version。
    """
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    call_count = {"n": 0}

    async def fake(**kwargs: Any) -> Any:
        call_count["n"] += 1
        return {
            "choices": [{"message": {"role": "assistant", "content": f"回复-{call_count['n']}"}}]
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        first = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
        assert first.status_code == 201
        first_id = first.json()["id"]
        assert first.json()["version"] == 1

        second = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
        assert second.status_code == 200
        assert second.json()["id"] == first_id
        assert second.json()["final_text"] == "回复-2"
        assert second.json()["version"] == 2

    assert call_count["n"] == 2

    # 删除草稿后可重新生成
    draft_id = first.json()["id"]
    deleted = client.delete(
        f"/api/tasks/{task_id}/drafts/{draft_id}",
        headers=created_user.headers,
    )
    assert deleted.status_code == 204
    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        third = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
        assert third.status_code == 201
    assert call_count["n"] == 3


def test_generate_draft_then_edit_then_submit(client, created_user, created_key) -> None:
    """LLM 草稿可被人工编辑后正式提交回复（首胜语义保持）。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    async def fake(**kwargs: Any) -> Any:
        return {
            "choices": [{"message": {"role": "assistant", "content": "上游草稿"}}],
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        generated = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        ).json()
    draft_id = generated["id"]

    # 编辑：追加最终文本
    edit = client.patch(
        f"/api/tasks/{task_id}/drafts/{draft_id}",
        headers=created_user.headers,
        json={
            "final_text": "最终回复（已人工编辑）",
            "expected_version": generated["version"],
        },
    )
    assert edit.status_code == 200
    assert edit.json()["final_text"] == "最终回复（已人工编辑）"

    # 提交回复（与 M6-B 一致）
    submit = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={
            "final_text": "最终回复（已人工编辑）",
            "source_draft_id": int(draft_id),
        },
    )
    assert submit.status_code == 201
    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["result_draft"]["final_text"] == "最终回复（已人工编辑）"


# ----------------------------------------------------------------------
# 同协议 Anthropic
# ----------------------------------------------------------------------


def _make_anthropic_waiting_task(
    client, key_id: int, user_id: int, *, content: str = "hello"
) -> int:
    import app.core.db as database
    from app.domain.enums import InferenceProtocol
    from app.protocols import anthropic as anthropic_protocol
    from app.repositories.models import ApiKey, User
    from app.services.inference_service import InferenceService

    payload = {
        "model": "deepseek-v4-pro",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": content}],
    }
    raw = json.dumps(payload).encode()
    parsed = anthropic_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, user_id)
        task = InferenceService().create_task(
            session,
            key=key,
            owner=owner,
            protocol=InferenceProtocol.ANTHROPIC_MESSAGES,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        assert task.id is not None
        return task.id


def test_generate_anthropic_with_anthropic_llm(client, created_user, created_key) -> None:
    task_id = _make_anthropic_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(
            name="claude-upstream",
            protocol="anthropic_messages",
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet",
        ),
    )

    async def fake(**kwargs: Any) -> Any:
        return {
            "id": "msg_test",
            "content": [
                {"type": "thinking", "thinking": "推理中"},
                {"type": "text", "text": "上游文本"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {"q": "weather"},
                },
            ],
        }

    with patch("app.services.llm_upstream.post_anthropic_messages", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reasoning"] == "推理中"
    assert body["final_text"] == "上游文本"
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "lookup"
    assert body["tool_calls"][0]["arguments"] == {"q": "weather"}


# ----------------------------------------------------------------------
# 跨协议 / 配置校验
# ----------------------------------------------------------------------


def test_cross_protocol_generation_chat_to_anthropic(client, created_user, created_key) -> None:
    """M7-D：Chat 任务 + Anthropic LLM 跨协议生成经 cross 矩阵转换成功。"""
    task_id = _make_waiting_task(
        client, created_key.id, created_user.user_id, content="hello cross"
    )
    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(
            name="anthropic-cross",
            protocol="anthropic_messages",
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet",
        ),
    )

    async def fake(**kwargs: Any) -> Any:
        # 验证 cross 矩阵转换：Anthropic 请求体结构正确
        body = kwargs["request_body"]
        assert body["model"] == "claude-3-5-sonnet"
        assert body["max_tokens"] == 1024
        assert {"role": "user", "content": "hello cross"} in body["messages"]
        return {
            "id": "msg_cross",
            "content": [{"type": "text", "text": "跨协议回答"}],
        }

    with patch("app.services.llm_upstream.post_anthropic_messages", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["final_text"] == "跨协议回答"
    assert resp.json()["source"] == "llm"


def test_cross_protocol_generation_rejects_unequivalent_fields(
    client, created_user, created_key
) -> None:
    """跨协议不可等价字段（reasoning 控制 / 结构化输出）返回 400。"""
    from sqlalchemy import update as sa_update

    import app.core.db as database
    from app.repositories.models import RequestTask

    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    # 注入 response_format 到规范化 options（模拟 Chat 结构化输出请求）
    with database.SessionLocal() as session:
        normalized = json.loads(session.get(RequestTask, task_id).normalized_request_json)
        normalized["options"]["response_format"] = {"type": "json_object"}
        session.execute(
            sa_update(RequestTask)
            .where(RequestTask.id == task_id)
            .values(normalized_request_json=json.dumps(normalized, ensure_ascii=False))
        )
        session.commit()

    cfg = _create_llm_config(
        client,
        created_user.headers,
        _llm_body(
            name="anthropic-strict",
            protocol="anthropic_messages",
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet",
        ),
    )
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code == 400
    assert "response_format" in resp.json()["error"]["message"]


def test_generate_with_disabled_config_returns_400(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body(enabled=False))
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code == 400
    assert "已停用" in resp.text


def test_generate_with_other_user_config_returns_404(
    client, admin_headers, created_user, created_key
) -> None:
    """他人的 LLM 配置不能用于本用户生成。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)

    other_username = f"o-{__import__('secrets').token_hex(3)}"
    other_created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": other_username,
            "display_name": other_username,
            "password": "User-Pass1!",
        },
    )
    assert other_created.status_code == 201
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert other_login.status_code == 200
    change = client.post(
        "/api/account/password",
        headers={"Authorization": f"Bearer {other_login.json()['access_token']}"},
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    other_cfg = _create_llm_config(client, other_headers, _llm_body(name="o-llm"))

    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": int(other_cfg["id"])},
    )
    assert resp.status_code == 404


def test_generate_after_task_resolved_returns_409(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    # 终结任务（提交回复）
    submit = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={"final_text": "done"},
    )
    assert submit.status_code == 201

    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code == 409


def test_generate_with_unknown_config_returns_404(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": 999999},
    )
    assert resp.status_code == 404


def test_generate_with_other_user_task_returns_404(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    other_username = f"o-{__import__('secrets').token_hex(3)}"
    client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": other_username,
            "display_name": other_username,
            "password": "User-Pass1!",
        },
    )
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert other_login.status_code == 200
    change = client.post(
        "/api/account/password",
        headers={"Authorization": f"Bearer {other_login.json()['access_token']}"},
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    other_login = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=other_headers,
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# 上游错误传播
# ----------------------------------------------------------------------


def test_upstream_timeout_returns_504(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    from app.domain.errors import DomainError, DomainErrorCode

    async def fake(**kwargs: Any) -> Any:
        raise DomainError(
            DomainErrorCode.REQUEST_TIMEOUT,
            "上游 LLM 请求超时",
            status_code=504,
        )

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 504


def test_upstream_response_error_returns_502(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    from app.domain.errors import DomainError, DomainErrorCode

    async def fake(**kwargs: Any) -> Any:
        raise DomainError(
            DomainErrorCode.UPSTREAM_ERROR,
            "上游 LLM 网络错误",
            status_code=502,
        )

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"])},
        )
    assert resp.status_code == 502


def test_upstream_chat_completions_url_and_body() -> None:
    """构造的 Chat 请求应包含 model / messages / system 注入首位。"""
    from app.services.llm_draft_service import _build_chat_request

    normalized = {
        "context": [
            {"role": "user", "content": "hi"},
        ],
        "instructions": "你是助手",
    }
    body = _build_chat_request(real_model="gpt-4o-mini", normalized=normalized)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][0] == {"role": "system", "content": "你是助手"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_upstream_anthropic_url_and_body() -> None:
    """构造的 Anthropic 请求应包含 system / max_tokens。"""
    from app.services.llm_draft_service import _build_anthropic_request

    normalized = {
        "context": [
            {"role": "user", "content": "hi"},
        ],
        "instructions": "你是助手",
    }
    body = _build_anthropic_request(
        real_model="claude-3-5-sonnet", normalized=normalized, max_tokens=512
    )
    assert body["model"] == "claude-3-5-sonnet"
    assert body["system"] == "你是助手"
    assert body["max_tokens"] == 512
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_parse_chat_response_with_no_tool_calls() -> None:
    from app.services.llm_draft_service import _parse_chat_response

    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "answer"}},
        ]
    }
    draft = _parse_chat_response(payload)
    assert draft.final_text == "answer"
    assert draft.reasoning is None
    assert draft.tool_calls == []


def test_parse_anthropic_response_text_only() -> None:
    from app.services.llm_draft_service import _parse_anthropic_response

    payload = {
        "content": [{"type": "text", "text": "answer"}],
    }
    draft = _parse_anthropic_response(payload)
    assert draft.final_text == "answer"
    assert draft.tool_calls == []


def test_generate_requires_auth(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code == 401


def test_generate_validates_input_min_id(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": 0},
    )
    assert resp.status_code == 422


def test_anthropic_generation_with_no_instructions_omits_system() -> None:
    from app.services.llm_draft_service import _build_anthropic_request

    normalized = {"context": [{"role": "user", "content": "hi"}], "instructions": None}
    body = _build_anthropic_request(real_model="claude", normalized=normalized, max_tokens=256)
    assert "system" not in body


def test_chat_request_omits_options_when_empty(client, created_user, created_key) -> None:
    """无 options 时不要污染 Chat 请求体。"""
    from app.services.llm_draft_service import _build_chat_request

    normalized = {
        "context": [{"role": "user", "content": "hi"}],
        "instructions": None,
        "options": None,
        "tools": None,
        "tool_choice": None,
    }
    body = _build_chat_request(real_model="gpt-4o-mini", normalized=normalized)
    assert body["model"] == "gpt-4o-mini"
    assert "tools" not in body
    assert "tool_choice" not in body


def test_chat_request_includes_options_and_tools() -> None:
    from app.services.llm_draft_service import _build_chat_request

    normalized = {
        "context": [{"role": "user", "content": "hi"}],
        "instructions": None,
        "tools": [{"type": "function", "function": {"name": "x"}}],
        "tool_choice": "auto",
        "options": {"temperature": 0.5, "top_p": 0.9},
    }
    body = _build_chat_request(real_model="gpt-4o-mini", normalized=normalized)
    assert body["tools"] == [{"type": "function", "function": {"name": "x"}}]
    assert body["tool_choice"] == "auto"
    assert body["temperature"] == 0.5
    assert body["top_p"] == 0.9


def test_anthropic_request_includes_system_blocks() -> None:
    """system_blocks（结构化系统块）原样透传。"""
    from app.services.llm_draft_service import _build_anthropic_request

    blocks = [{"type": "text", "text": "block1"}, {"type": "text", "text": "block2"}]
    body = _build_anthropic_request(
        real_model="claude",
        normalized={"context": [], "instructions": None, "system_blocks": blocks},
        max_tokens=128,
    )
    assert body["system"] == blocks


def test_anthropic_request_uses_max_tokens_when_normalized_missing() -> None:
    """max_tokens 缺失时回退到 service 层传入的 max_tokens。"""
    from app.services.llm_draft_service import _build_anthropic_request

    body = _build_anthropic_request(
        real_model="claude",
        normalized={"context": [], "instructions": None},
        max_tokens=2048,
    )
    assert body["max_tokens"] == 2048


def test_chat_request_includes_instructions_at_first_position() -> None:
    """system 指令必须排首位（在历史 context 之前）。"""
    from app.services.llm_draft_service import _build_chat_request

    normalized = {
        "context": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "prev"},
        ],
        "instructions": "Be concise",
    }
    body = _build_chat_request(real_model="gpt-4o-mini", normalized=normalized)
    assert body["messages"][0] == {"role": "system", "content": "Be concise"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_chat_response_with_assistant_tool_calls_only() -> None:
    """tool_calls-only 响应解析为空 final_text + 非空 tool_calls。"""
    from app.services.llm_draft_service import _parse_chat_response

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "x", "arguments": "{}"},
                        }
                    ],
                }
            }
        ]
    }
    draft = _parse_chat_response(payload)
    assert draft.final_text is None
    assert len(draft.tool_calls) == 1
    assert draft.tool_calls[0].id == "c1"


def test_anthropic_response_with_tool_use_only() -> None:
    from app.services.llm_draft_service import _parse_anthropic_response

    payload = {
        "content": [
            {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"k": "v"}},
        ]
    }
    draft = _parse_anthropic_response(payload)
    assert draft.final_text is None
    assert draft.tool_calls[0].id == "t1"
    assert draft.tool_calls[0].name == "lookup"
    assert draft.tool_calls[0].arguments == {"k": "v"}


def test_chat_response_raises_on_missing_choices() -> None:
    """上游响应缺 choices 时返 502 不暴露内部。"""
    from app.domain.errors import DomainError, DomainErrorCode
    from app.services.llm_draft_service import _parse_chat_response

    with pytest.raises(DomainError) as exc:
        _parse_chat_response({"choices": []})
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


def test_anthropic_response_raises_on_missing_content() -> None:
    from app.domain.errors import DomainError, DomainErrorCode
    from app.services.llm_draft_service import _parse_anthropic_response

    with pytest.raises(DomainError) as exc:
        _parse_anthropic_response({})
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


def test_chat_response_with_invalid_tool_arguments_falls_back_to_empty() -> None:
    """tool_calls.arguments 是非 JSON 字符串时回退到 {}。"""
    from app.services.llm_draft_service import _parse_chat_response

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "x", "arguments": "not-json"},
                        }
                    ],
                }
            }
        ]
    }
    draft = _parse_chat_response(payload)
    assert draft.tool_calls[0].arguments == {}


def test_generate_draft_requires_owner_to_be_active(
    client, admin_headers, created_user, created_key
) -> None:
    """禁用用户不能生成草稿（与 save_draft 一致语义）。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id)
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    client.patch(
        f"/api/users/{created_user.user_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={"llm_config_id": int(cfg["id"])},
    )
    assert resp.status_code in (401, 403)


# ----------------------------------------------------------------------
# M14+ 生成模式：reasoning / reply / both  + 上下文消息级勾选
# ----------------------------------------------------------------------


def _make_waiting_task_multi(
    client, key_id: int, user_id: int, *, messages: list[dict[str, Any]]
) -> int:
    """直接经编排服务创建一个 WAITING_HUMAN 任务，支持传入多条历史消息。"""
    import app.core.db as database
    from app.domain.enums import InferenceProtocol
    from app.protocols import chat_completions as chat_protocol
    from app.repositories.models import ApiKey, User
    from app.services.inference_service import InferenceService

    payload = {"model": "deepseek-v4-pro", "messages": messages}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, user_id)
        task = InferenceService().create_task(
            session,
            key=key,
            owner=owner,
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        assert task.id is not None
        return task.id


def test_generate_mode_reasoning_only_writes_into_reasoning(
    client, created_user, created_key
) -> None:
    """mode=reasoning：上游返回正文与推理，归入草稿 reasoning；final_text=None。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id, content="hi")
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    async def fake(**kwargs: Any) -> Any:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "用户问题的详细推理",
                    }
                }
            ]
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"]), "mode": "reasoning"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["final_text"] is None
    assert "推理" in (body["reasoning"] or "")
    assert body["tool_calls"] == []


def test_generate_mode_reply_preserves_user_reasoning_seed(
    client, created_user, created_key
) -> None:
    """mode=reply + reasoning_seed：上游只看种子写回复，草稿保留种子作为 reasoning。"""
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id, content="hi")
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        captured["body"] = kwargs["request_body"]
        return {"choices": [{"message": {"role": "assistant", "content": "已写好的回复"}}]}

    seed = "用户手写的思考链：先确认输入再展开"
    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={
                "llm_config_id": int(cfg["id"]),
                "mode": "reply",
                "reasoning_seed": seed,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["final_text"] == "已写好的回复"
    assert body["reasoning"] == seed
    # 种子被拼入上游 system 消息
    sys_msg = captured["body"]["messages"][0]
    assert sys_msg["role"] == "system"
    assert seed in sys_msg["content"]


def test_generate_exclude_context_indices_omits_history(client, created_user, created_key) -> None:
    """消息级勾选：被排除的 normalized context 下标不会送入上游。"""
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]
    task_id = _make_waiting_task_multi(
        client, created_key.id, created_user.user_id, messages=messages
    )
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        captured["body"] = kwargs["request_body"]
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={
                "llm_config_id": int(cfg["id"]),
                "mode": "reply",
                "exclude_context_indices": [1],  # 排除 normalized context[1]（第一问）
            },
        )
    assert resp.status_code == 201, resp.text
    sent = captured["body"]["messages"]
    # 第一问（被排除）不应出现；其他上下文仍存在。
    joined = "\n".join(str(m.get("content", "")) for m in sent)
    assert "第一问" not in joined
    assert "第一答" in joined
    assert "第二问" in joined


def test_generate_exclude_invalid_index_returns_400(client, created_user, created_key) -> None:
    """exclude_context_indices 越界直接 400，避免静默丢弃过滤。"""
    task_id = _make_waiting_task_multi(
        client,
        created_key.id,
        created_user.user_id,
        messages=[
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ],
    )
    cfg = _create_llm_config(client, created_user.headers, _llm_body())
    resp = client.post(
        f"/api/tasks/{task_id}/drafts/generate",
        headers=created_user.headers,
        json={
            "llm_config_id": int(cfg["id"]),
            "exclude_context_indices": [99],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "下标越界" in resp.json()["error"]["message"]


def test_save_draft_with_tool_calls_blocked_when_sandbox_unavailable(
    client, created_user, created_key, monkeypatch
) -> None:
    """无沙箱环境：草稿保存拒绝携带 tool_calls（public_code=sandbox_unavailable）。"""
    monkeypatch.setattr("app.services.task_service.fake_tool_calls_allowed", lambda: False)
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id, content="hi")
    resp = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json={
            "reasoning": None,
            "tool_calls": [{"id": "call_01", "name": "x", "arguments": {}}],
            "final_text": "ok",
        },
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "validation_failed"
    assert "沙箱不可用" in body["error"]["message"]


def test_submit_reply_with_tool_calls_blocked_when_sandbox_unavailable(
    client, created_user, created_key, monkeypatch
) -> None:
    """无沙箱环境：直接回复提交同样拒绝携带 tool_calls。"""
    monkeypatch.setattr("app.services.task_service.fake_tool_calls_allowed", lambda: False)
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id, content="hi")
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={
            "reasoning": None,
            "tool_calls": [{"id": "call_01", "name": "x", "arguments": {}}],
            "final_text": "ok",
            "source_draft_id": None,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "沙箱不可用" in resp.json()["error"]["message"]


def test_generate_strips_tool_calls_when_sandbox_unavailable(
    client, created_user, created_key, monkeypatch
) -> None:
    """无沙箱环境：LLM 草稿生成也会丢弃上游返回的 tool_calls。"""
    monkeypatch.setattr("app.services.task_service.fake_tool_calls_allowed", lambda: False)
    task_id = _make_waiting_task(client, created_key.id, created_user.user_id, content="hi")
    cfg = _create_llm_config(client, created_user.headers, _llm_body())

    async def fake(**kwargs: Any) -> Any:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        "tool_calls": [
                            {
                                "id": "call_01",
                                "type": "function",
                                "function": {"name": "x", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }

    with patch("app.services.llm_upstream.post_chat_completions", side_effect=fake):
        resp = client.post(
            f"/api/tasks/{task_id}/drafts/generate",
            headers=created_user.headers,
            json={"llm_config_id": int(cfg["id"]), "mode": "both"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tool_calls"] == []


def test_conversation_includes_context_index(client, created_user, created_key) -> None:
    """GET /conversation 返回的每条消息携带 context_index（系统指令为 None）。"""
    task_id = _make_waiting_task_multi(
        client,
        created_key.id,
        created_user.user_id,
        messages=[
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
        ],
    )
    resp = client.get(f"/api/tasks/{task_id}/conversation", headers=created_user.headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["messages"]
    assert items, "project_messages 应至少返回 2 条历史消息"
    indexes = [item["context_index"] for item in items]
    # 系统指令块 context_index 为 None；normalized context 条目按 0..n 标记。
    assert [i for i in indexes if i is not None] == [0, 1]

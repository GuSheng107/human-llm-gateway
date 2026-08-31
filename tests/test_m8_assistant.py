"""M8-A Web 小助手后端测试（docs/API_CONTRACT.md §10）。

覆盖：
- 会话 CRUD + owner 隔离（他人会话 404）
- 消息发送：user 消息落库 + LLM 回复落库 + last_message_at 更新
- 页面上下文封闭 schema：未知 feature / resource 键 / 未知字段拒收
- 脱敏：resource 值 / unsaved_edit 文本 / tool_call arguments 值擦洗
- 上下文快照落库且历史不回写
- LLM 调用 mock：Chat 与 Anthropic 双协议、历史轮次携带
- 会话未绑定/停用配置 400；未配置调用 400
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import app.core.db as database
from app.repositories.models import AssistantMessage
from app.services.llm_upstream import UpstreamChunk

_UPSTREAM_CHAT = "app.services.llm_upstream.post_chat_completions"
_UPSTREAM_ANTHROPIC = "app.services.llm_upstream.post_anthropic_messages"
_UPSTREAM_STREAM_CHAT = "app.services.llm_upstream.stream_chat_completions"


def _llm_body(name: str = "assistant-cfg") -> dict[str, Any]:
    return {
        "name": name,
        "protocol": "openai_chat",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-assistant",
        "model": "gpt-4o-mini",
        "timeout_seconds": 60,
    }


def _make_llm_config(client, headers, name: str = "assistant-cfg") -> dict[str, Any]:
    resp = client.post("/api/llm-configs", headers=headers, json=_llm_body(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_session(client, headers, llm_config_id: int | None) -> dict[str, Any]:
    resp = client.post(
        "/api/assistant/sessions",
        headers=headers,
        json={"title": "测试会话", "llm_config_id": llm_config_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _chat_reply(**kwargs: Any) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": "小助手回答"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


# ----------------------------------------------------------------------
# 会话 CRUD 与隔离
# ----------------------------------------------------------------------


def test_session_crud_roundtrip(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    created = _make_session(client, created_user.headers, int(cfg["id"]))
    assert created["title"] == "测试会话"
    assert created["llm_config_id"] == cfg["id"]

    listing = client.get("/api/assistant/sessions", headers=created_user.headers)
    assert listing.status_code == 200
    assert any(s["id"] == created["id"] for s in listing.json())

    detail = client.get(f"/api/assistant/sessions/{created['id']}", headers=created_user.headers)
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    deleted = client.delete(
        f"/api/assistant/sessions/{created['id']}", headers=created_user.headers
    )
    assert deleted.status_code == 204
    listing2 = client.get("/api/assistant/sessions", headers=created_user.headers)
    assert all(s["id"] != created["id"] for s in listing2.json())
    gone = client.get(f"/api/assistant/sessions/{created['id']}", headers=created_user.headers)
    assert gone.status_code == 404


def test_session_owner_isolation(client, admin_headers, created_user) -> None:
    """他人会话对当前用户 404（防存在性探测）。"""
    cfg = _make_llm_config(client, created_user.headers)
    created = _make_session(client, created_user.headers, int(cfg["id"]))
    other = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "assistant-other",
            "display_name": "assistant-other",
            "password": "User-Pass1!",
        },
    )
    assert other.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={
            "username": "assistant-other",
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    change = client.post(
        "/api/account/password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    login2 = client.post(
        "/api/auth/login",
        json={
            "username": "assistant-other",
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {login2.json()['access_token']}"}

    assert (
        client.get(f"/api/assistant/sessions/{created['id']}", headers=other_headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/assistant/sessions/{created['id']}/messages",
            headers=other_headers,
            json={"text": "hi"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/assistant/sessions/{created['id']}", headers=other_headers).status_code
        == 404
    )


def test_session_with_foreign_llm_config_rejected(client, admin_headers, created_user) -> None:
    """别人的 LLM 配置不能绑定会话。"""
    other_cfg = _make_llm_config(client, created_user.headers)
    other = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "assistant-foreign",
            "display_name": "assistant-foreign",
            "password": "User-Pass1!",
        },
    )
    assert other.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={
            "username": "assistant-foreign",
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    change = client.post(
        "/api/account/password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert change.status_code == 200
    login2 = client.post(
        "/api/auth/login",
        json={
            "username": "assistant-foreign",
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    other_headers = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    resp = client.post(
        "/api/assistant/sessions",
        headers=other_headers,
        json={"title": "x", "llm_config_id": int(other_cfg["id"])},
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# 消息发送与上下文
# ----------------------------------------------------------------------


def test_send_message_with_context_and_reply(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))

    with patch(_UPSTREAM_CHAT, side_effect=lambda **kw: _chat_reply(**kw)):
        resp = client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={
                "text": "帮我看看这个任务怎么回复",
                "page_context": {
                    "route": "/tasks/1",
                    "feature": "task_detail",
                    "resource": {"task_id": "1", "state": "waiting_human"},
                    "unsaved_edit": {
                        "reasoning": "先分析请求",
                        "final_text": "这是我的草稿",
                        "tool_calls": [],
                    },
                    "context_version": 1,
                },
            },
        )
    assert resp.status_code == 201, resp.text
    reply = resp.json()
    assert reply["role"] == "assistant"
    assert reply["text"] == "小助手回答"

    detail = client.get(
        f"/api/assistant/sessions/{session_data['id']}", headers=created_user.headers
    ).json()
    messages = detail["messages"]
    assert len(messages) == 2  # user + assistant
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert user_msg["page_context"] is not None
    assert user_msg["page_context"]["feature"] == "task_detail"
    assert user_msg["page_context"]["resource"]["state"] == "waiting_human"
    assert user_msg["page_context"]["unsaved_edit"]["final_text"] == "这是我的草稿"
    assert messages[1]["page_context"] is None  # 回复不携带上下文


def test_context_unknown_feature_rejected(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={
            "text": "hi",
            "page_context": {"feature": "not_a_feature", "resource": {}},
        },
    )
    assert resp.status_code == 400


def test_context_unknown_resource_key_rejected(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={
            "text": "hi",
            "page_context": {
                "feature": "task_detail",
                "resource": {"password": "x"},  # 键不在白名单
            },
        },
    )
    assert resp.status_code == 400
    assert "password" in resp.json()["error"]["message"]


def test_context_unknown_field_rejected(client, created_user) -> None:
    """StrictModel：schema 外字段 422 拒收。"""
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={
            "text": "hi",
            "page_context": {
                "feature": "task_detail",
                "resource": {},
                "evil_field": "anything",
            },
        },
    )
    assert resp.status_code == 422


# ----------------------------------------------------------------------
# 脱敏
# ----------------------------------------------------------------------


def test_context_secrets_redacted(client, created_user) -> None:
    """resource 值与自由文本中的凭据形态被擦洗为 [REDACTED]。"""
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))
    with patch(_UPSTREAM_CHAT, side_effect=lambda **kw: _chat_reply(**kw)):
        resp = client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={
                "text": "我的密钥是 sk-abcdefghij1234567890 怎么用",
                "page_context": {
                    "feature": "task_detail",
                    "resource": {
                        "task_id": "1",
                        "model": "sk-proj1234567890abcdefgh",
                    },
                    "unsaved_edit": {
                        "reasoning": "token=abcdef1234567890abcdef",
                        "final_text": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "name": "fn",
                                "arguments": {"api_key": "sk-zzzzzz1234567890xxx"},
                            }
                        ],
                    },
                },
            },
        )
    assert resp.status_code == 201, resp.text
    detail = client.get(
        f"/api/assistant/sessions/{session_data['id']}", headers=created_user.headers
    ).json()
    user_msg = detail["messages"][0]
    context = user_msg["page_context"]
    assert context["resource"]["model"] == "[REDACTED]"
    edit = context["unsaved_edit"]
    assert edit["reasoning"] == "[REDACTED]"
    assert edit["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
    # 键结构保留
    assert "api_key" in edit["tool_calls"][0]["arguments"]
    # 落库内容不含明文凭据
    with database.SessionLocal() as session:
        row = session.get(AssistantMessage, int(user_msg["id"]))
        assert "sk-abcdefghij" not in (row.page_context_json or "")
        assert "sk-zzzzzz" not in (row.page_context_json or "")


def test_user_text_also_redacted_before_upstream(client, created_user) -> None:
    """发送文本（落库原文保留用户语义，但送上游前同样擦洗）。"""
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))

    captured: dict[str, Any] = {}

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["request_body"]["messages"]
        return _chat_reply(**kwargs)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        resp = client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={"text": "key 是 sk-abcdef1234567890XYZ 帮我测一下"},
        )
    assert resp.status_code == 201
    upstream_text = captured["messages"][-1]["content"]
    assert "sk-abcdef1234567890XYZ" not in upstream_text
    assert "[REDACTED]" in upstream_text


# ----------------------------------------------------------------------
# LLM 调用
# ----------------------------------------------------------------------


def test_history_carried_to_upstream(client, created_user) -> None:
    """第二轮对话携带第一轮历史。"""
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))

    captured: dict[str, Any] = {}

    async def fake_post(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["request_body"]["messages"]
        return _chat_reply(**kwargs)

    with patch(_UPSTREAM_CHAT, side_effect=fake_post):
        client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={"text": "第一轮"},
        )
        client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={"text": "第二轮"},
        )
    texts = [m["content"] for m in captured["messages"]]
    assert any("第一轮" in t for t in texts)
    assert any("小助手回答" in t for t in texts)


def test_anthropic_config_reply_extracted(client, created_user) -> None:
    """Anthropic 配置：上游响应 content 块提取回复。"""
    resp_cfg = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json={
            "name": "assistant-anthropic",
            "protocol": "anthropic_messages",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant",
            "model": "claude-3-5-sonnet",
            "timeout_seconds": 60,
        },
    )
    assert resp_cfg.status_code == 201
    cfg = resp_cfg.json()
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))

    async def fake_anthropic(**kwargs: Any) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": "Anthropic 回答"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }

    with patch(_UPSTREAM_ANTHROPIC, side_effect=fake_anthropic):
        resp = client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages",
            headers=created_user.headers,
            json={"text": "hi"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["text"] == "Anthropic 回答"


def test_send_without_bound_config_rejected(client, created_user) -> None:
    """会话未绑定 LLM 配置时发送 400。"""
    session_data = _make_session(client, created_user.headers, None)
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={"text": "hi"},
    )
    assert resp.status_code == 400


def test_send_with_disabled_config_rejected(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    disabled = client.patch(
        f"/api/llm-configs/{cfg['id']}",
        headers=created_user.headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    # 停用配置在会话创建与消息发送两个入口都被拒绝。
    create_resp = client.post(
        "/api/assistant/sessions",
        headers=created_user.headers,
        json={"title": "x", "llm_config_id": int(cfg["id"])},
    )
    assert create_resp.status_code == 400
    # 既有会话（配置后来停用）发送消息同样被拒绝。
    enabled_cfg = _make_llm_config(client, created_user.headers, "second-cfg")
    session_data = _make_session(client, created_user.headers, int(enabled_cfg["id"]))
    disable2 = client.patch(
        f"/api/llm-configs/{enabled_cfg['id']}",
        headers=created_user.headers,
        json={"enabled": False},
    )
    assert disable2.status_code == 200
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={"text": "hi"},
    )
    assert resp.status_code == 400


def test_empty_message_rejected(client, created_user) -> None:
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages",
        headers=created_user.headers,
        json={"text": "   "},
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# SSE 流式
# ----------------------------------------------------------------------


def test_stream_message_delta_and_done(client, created_user) -> None:
    """SSE 流式：delta 增量 + done 完整消息；两条消息均落库。"""
    cfg = _make_llm_config(client, created_user.headers)
    session_data = _make_session(client, created_user.headers, int(cfg["id"]))

    async def fake_stream(**kwargs: Any):
        for piece in ("小助手", "回答"):
            yield UpstreamChunk(text=piece)

    with patch(_UPSTREAM_STREAM_CHAT, side_effect=fake_stream):
        resp = client.post(
            f"/api/assistant/sessions/{session_data['id']}/messages/stream",
            headers=created_user.headers,
            json={"text": "hi"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert '"type": "delta"' in body
    assert '"text": "小助手"' in body
    assert '"type": "done"' in body
    assert "小助手回答" in body

    detail = client.get(
        f"/api/assistant/sessions/{session_data['id']}", headers=created_user.headers
    ).json()
    messages = detail["messages"]
    assert len(messages) == 2  # user + assistant
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["text"] == "小助手回答"


def test_stream_message_error_event(client, created_user) -> None:
    """流中 DomainError → error 事件（HTTP 仍为 200）；user 消息保留。"""
    session_data = _make_session(client, created_user.headers, None)  # 未绑定配置
    resp = client.post(
        f"/api/assistant/sessions/{session_data['id']}/messages/stream",
        headers=created_user.headers,
        json={"text": "hi"},
    )
    assert resp.status_code == 200
    assert '"type": "error"' in resp.text
    assert "validation_failed" in resp.text
    # user 消息在错误前已落库（便于用户重试）。
    detail = client.get(
        f"/api/assistant/sessions/{session_data['id']}", headers=created_user.headers
    ).json()
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"

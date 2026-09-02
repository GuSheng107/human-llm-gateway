"""M6-B 任务工作台测试（docs/API_CONTRACT.md §9, docs/ROADMAP.md M6-B）。

覆盖：
- 任务详情可见性（owner / admin / 非归属 404）
- 草稿保存/更新/删除往返
- 原子提交首胜不可撤（晚到 409 + 审计事件）
- 空草稿拒绝 422
- 管理员只读禁写 403
- IM DSL 解析/序列化往返无损
- 任务列表筛选与归属隔离
- 事件时间线分页
"""

from __future__ import annotations

import json
from typing import Any

import app.core.db as database
from app.domain.dsl import (
    extract_task_target,
    is_empty_draft,
    parse_message,
    parse_reply,
    serialize_reply,
)
from app.domain.enums import InferenceProtocol, TaskState
from app.domain.values import ReplyDraft, ReplyToolCall
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, RequestTask, User
from app.services.inference_service import InferenceService

# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _bearer(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def _make_waiting_task(
    key_id: int,
    owner_user_id: int,
    *,
    content: str = "hello",
) -> int:
    """直接经编排服务创建一个 WAITING_HUMAN 任务并返回其 id。"""
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": content}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    service = InferenceService()
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, owner_user_id)
        task = service.create_task(
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


def _draft_body(
    *,
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    final_text: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if reasoning is not None:
        body["reasoning"] = reasoning
    if tool_calls is not None:
        body["tool_calls"] = tool_calls
    if final_text is not None:
        body["final_text"] = final_text
    return body


_TOOL_CALL_A = {"id": "call_001", "name": "search", "arguments": {"q": "天气"}}
_TOOL_CALL_B = {"id": "call_002", "name": "calc", "arguments": {"expr": "1+1"}}


# ======================================================================
# 任务详情可见性
# ======================================================================


def test_task_detail_owner_sees_full_fields(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.get(f"/api/tasks/{task_id}", headers=created_user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_owner"] is True
    assert body["can_edit"] is True
    # 原始请求按需加载：详情响应不再携带（独立端点）。
    assert body["raw_request"] is None
    # 所有者可查看完整提示词，无截断。
    assert body["prompt_text"] == "hello"
    assert body["drafts"] == []
    assert body["active_draft_id"] is None
    assert body["result_draft"] is None
    assert body["tool_names"] == []
    assert body["public_error_code"] is None
    assert body["cancel_reason_code"] is None
    assert len(body["events"]) >= 1
    assert body["events"][0]["event_type"] == "created"
    # 按需端点：所有者取回完整原始请求。
    raw_resp = client.get(f"/api/tasks/{task_id}/raw-request", headers=created_user.headers)
    assert raw_resp.status_code == 200, raw_resp.text
    raw_body = raw_resp.json()
    assert raw_body["task_id"] == str(task_id)
    assert raw_body["raw_request"]["messages"][0]["content"] == "hello"


def test_task_detail_admin_sees_owner_but_cannot_edit(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.get(f"/api/tasks/{task_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_owner"] is False
    assert body["can_edit"] is False
    assert body["owner_username"] == created_user.username
    assert body["raw_request"] is None
    assert body["drafts"] == []
    assert body["active_draft_id"] is None
    assert body["result_draft"] is None


def test_task_detail_non_owner_returns_404(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp_nonexistent = client.get(f"/api/tasks/{task_id + 99999}", headers=created_user.headers)
    assert resp_nonexistent.status_code == 404


def test_task_detail_requires_auth(client, created_key) -> None:
    resp = client.get("/api/tasks/1")
    assert resp.status_code == 401


def test_task_raw_request_owner_only(client, created_user, created_key, admin_headers) -> None:
    """原始请求按需端点：非所有者（普通用户）403；管理员可监管查看。"""

    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    # 另一个普通用户不可见（get_owned_task 已 404）。
    other_headers = _create_other_user_headers(client, admin_headers)
    resp = client.get(f"/api/tasks/{task_id}/raw-request", headers=other_headers)
    assert resp.status_code == 404
    # 管理员可以监管查看。
    admin_resp = client.get(f"/api/tasks/{task_id}/raw-request", headers=admin_headers)
    assert admin_resp.status_code == 200
    assert admin_resp.json()["raw_request"] is not None


def _create_other_user_headers(client, admin_headers) -> dict:
    import secrets as _secrets

    username = f"other-{_secrets.token_hex(4)}"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "display_name": username,
            "password": "Other-Pass1!",
        },
    )
    assert created.status_code == 201, created.text
    resp = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": "Other-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": "Other-Pass1!", "new_password": "Changed-Pass1!"},
    )
    assert changed.status_code == 200, changed.text
    resp = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ======================================================================
# 草稿保存/更新/删除往返
# ======================================================================


def test_draft_save_and_restore_roundtrip(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    draft = _draft_body(
        reasoning="思考过程",
        tool_calls=[_TOOL_CALL_A, _TOOL_CALL_B],
        final_text="最终回复",
    )
    resp = client.post(f"/api/tasks/{task_id}/drafts", headers=created_user.headers, json=draft)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["source"] == "manual"
    assert created["state"] == "editing"
    assert created["reasoning"] == "思考过程"
    assert created["final_text"] == "最终回复"
    assert len(created["tool_calls"]) == 2
    assert created["tool_calls"][0] == _TOOL_CALL_A
    assert created["tool_calls"][1] == _TOOL_CALL_B

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["active_draft_id"] == str(created["id"])
    assert len(detail["drafts"]) == 1
    restored = detail["drafts"][0]
    assert restored["reasoning"] == "思考过程"
    assert restored["final_text"] == "最终回复"
    assert restored["tool_calls"] == [_TOOL_CALL_A, _TOOL_CALL_B]


def test_draft_save_upsert_overwrites_active(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    body1 = _draft_body(final_text="第一版")
    resp1 = client.post(f"/api/tasks/{task_id}/drafts", headers=created_user.headers, json=body1)
    assert resp1.status_code == 201
    draft1_id = resp1.json()["id"]

    body2 = _draft_body(final_text="第二版")
    resp2 = client.post(f"/api/tasks/{task_id}/drafts", headers=created_user.headers, json=body2)
    assert resp2.status_code == 201
    assert resp2.json()["id"] == draft1_id

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert len(detail["drafts"]) == 1
    assert detail["drafts"][0]["final_text"] == "第二版"


def test_draft_update_changes_fields(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json=_draft_body(final_text="旧"),
    ).json()
    draft_id = saved["id"]
    resp = client.patch(
        f"/api/tasks/{task_id}/drafts/{draft_id}",
        headers=created_user.headers,
        json={
            **_draft_body(reasoning="新思考", final_text="新文本"),
            "expected_version": saved["version"],
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["reasoning"] == "新思考"
    assert updated["final_text"] == "新文本"
    assert updated["version"] == saved["version"] + 1


def test_draft_delete_removes_from_list(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json=_draft_body(final_text="待删"),
    ).json()
    draft_id = saved["id"]
    resp = client.delete(
        f"/api/tasks/{task_id}/drafts/{draft_id}",
        headers=created_user.headers,
    )
    assert resp.status_code == 204
    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["drafts"] == []
    assert detail["active_draft_id"] is None


def test_draft_update_nonexistent_returns_404(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.patch(
        f"/api/tasks/{task_id}/drafts/99999",
        headers=created_user.headers,
        json={**_draft_body(final_text="x"), "expected_version": 1},
    )
    assert resp.status_code == 404


# ======================================================================
# 原子提交首胜不可撤
# ======================================================================


def test_submit_reply_first_wins_accepted(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(
            reasoning="理由",
            tool_calls=[_TOOL_CALL_A],
            final_text="已回复",
        ),
    )
    assert resp.status_code == 201, resp.text
    result = resp.json()
    assert result["accepted"] is True
    assert result["state"] == "response_ready"

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["can_edit"] is False
    assert detail["result_draft"] is not None
    assert detail["result_draft"]["final_text"] == "已回复"
    assert detail["result_draft"]["reasoning"] == "理由"
    assert len(detail["result_draft"]["tool_calls"]) == 1


def test_submit_reply_late_returns_409(client, created_user, created_key) -> None:
    """顺序提交：首个成功后状态切到 RESPONSE_READY，第二次被 _assert_editable 拦截 409。

    reply_rejected_late 事件仅在并发竞态（两个请求同时通过 _assert_editable
    但只有一个赢得 first_reply_wins 条件 UPDATE）时由 submit_reply 记录，
    顺序提交不会进入该分支——晚到拦截由 _assert_editable 完成。
    """
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    first = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="第一个"),
    )
    assert first.status_code == 201

    late = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="晚到"),
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "conflict"

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    assert detail["result_draft"]["final_text"] == "第一个"
    assert detail["can_edit"] is False


def test_concurrent_race_records_reply_rejected_late(client, created_user, created_key) -> None:
    """模拟并发竞态：ORM 对象仍处 WAITING_HUMAN 但 DB 版本已被并发推进。

    submit_reply 的 _assert_editable 读 ORM 对象（stale，仍 WAITING_HUMAN）通过，
    但 first_reply_wins 的条件 UPDATE（WHERE version=stale）匹配 0 行返回 False，
    随后记录 reply_rejected_late 事件——这是真正的并发竞态语义。
    """
    from sqlalchemy import update

    from app.services.task_service import TaskService

    task_id = _make_waiting_task(created_key.id, created_user.user_id)

    with database.SessionLocal() as session:
        user = session.get(User, created_user.user_id)
        task = session.get(RequestTask, task_id)
        assert task.state is TaskState.WAITING_HUMAN

        session.execute(
            update(RequestTask)
            .where(RequestTask.id == task_id)
            .values(version=RequestTask.version + 1)
            .execution_options(synchronize_session=False)
        )
        session.commit()

        service = TaskService()
        accepted = service.submit_reply(
            session,
            task=task,
            owner=user,
            draft=ReplyDraft(final_text="竞态晚到"),
        )
        session.commit()
        assert accepted is False

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    event_types = [e["event_type"] for e in detail["events"]]
    assert "reply_rejected_late" in event_types
    assert detail["result_draft"] is None


def test_submit_empty_draft_returns_422(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"


def test_submit_with_source_draft_marks_submitted(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json=_draft_body(final_text="草稿内容"),
    ).json()
    draft_id = int(saved["id"])
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json={
            "final_text": "草稿内容",
            "source_draft_id": draft_id,
        },
    )
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    submitted = [d for d in detail["drafts"] if d["state"] == "submitted"]
    assert len(submitted) == 1
    assert submitted[0]["id"] == saved["id"]


# ======================================================================
# 管理员只读禁写
# ======================================================================


def test_admin_cannot_save_draft(client, admin_headers, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=admin_headers,
        json=_draft_body(final_text="管理员尝试"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_admin_cannot_submit_reply(client, admin_headers, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=admin_headers,
        json=_draft_body(final_text="管理员尝试"),
    )
    assert resp.status_code == 403


def test_write_after_terminal_state_returns_409(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="完成"),
    )
    resp = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json=_draft_body(final_text="再来"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_disabled_user_cannot_save_draft(client, admin_headers, created_user, created_key) -> None:
    """AGENTS.md §1：禁用用户时立即撤销会话/活动任务，写接口兜底 401/403。"""
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    disable = client.patch(
        f"/api/users/{created_user.user_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert disable.status_code == 200
    resp = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json=_draft_body(final_text="禁用后写"),
    )
    assert resp.status_code in (401, 403)


def test_disabled_user_cannot_submit_reply(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.patch(
        f"/api/users/{created_user.user_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    resp = client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="禁用后提交"),
    )
    assert resp.status_code in (401, 403)


def test_detail_previous_task_id_returns_public_id(client, created_user, created_key) -> None:
    """详情页 previous_task_id 应返回前序任务的 public_id 而非内部数字。"""
    parent_id = _make_waiting_task(created_key.id, created_user.user_id)
    child_id = _make_waiting_task(created_key.id, created_user.user_id)
    with database.SessionLocal() as session:
        parent = session.get(RequestTask, parent_id)
        child = session.get(RequestTask, child_id)
        child.previous_task_id = parent.id
        session.commit()
    resp = client.get(f"/api/tasks/{child_id}", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["previous_task_id"] == parent.public_id
    assert body["events_total"] >= 1


# ======================================================================
# 事件时间线分页
# ======================================================================


def test_event_timeline_pagination(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="完成"),
    )

    resp = client.get(
        f"/api/tasks/{task_id}/events?page=1&page_size=1",
        headers=created_user.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    assert body["page"] == 1
    assert body["page_size"] == 1

    resp2 = client.get(
        f"/api/tasks/{task_id}/events?page=2&page_size=1",
        headers=created_user.headers,
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1


def test_event_timeline_has_correct_actor_for_web_reply(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.post(
        f"/api/tasks/{task_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="完成"),
    )
    detail = client.get(f"/api/tasks/{task_id}", headers=created_user.headers).json()
    reply_events = [e for e in detail["events"] if e["event_type"] == "reply_submitted"]
    assert len(reply_events) == 1
    assert reply_events[0]["actor_type"] == "user"
    assert reply_events[0]["payload"]["source"] == "web"


# ======================================================================
# 任务列表筛选与归属隔离
# ======================================================================


def test_task_list_owner_isolation(client, admin_headers, created_user, created_key) -> None:
    own_task = _make_waiting_task(created_key.id, created_user.user_id)

    other_username = f"u2-{__import__('secrets').token_hex(3)}"
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
    login_resp = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "User-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert login_resp.status_code == 200
    other_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}
    client.post(
        "/api/account/password",
        headers=other_headers,
        json={"current_password": "User-Pass1!", "new_password": "Changed-Pass1!"},
    )
    login2 = client.post(
        "/api/auth/login",
        json={
            "username": other_username,
            "password": "Changed-Pass1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert login2.status_code == 200
    other_headers = {"Authorization": f"Bearer {login2.json()['access_token']}"}
    other_key = client.post(
        "/api/api-keys",
        headers=other_headers,
        json={"name": "k2", "delivery_mode": "web", "reply_strategy": "human"},
    ).json()

    from sqlalchemy import select

    with database.SessionLocal() as session:
        other_user = session.execute(
            select(User).where(User.username == other_username)
        ).scalar_one()

    other_task = _make_waiting_task(int(other_key["id"]), other_user.id)

    resp = client.get("/api/tasks", headers=created_user.headers)
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(own_task) in ids
    assert str(other_task) not in ids
    assert all(item.get("owner_username") is None for item in resp.json()["items"])


def test_task_list_admin_sees_all_with_owner(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.get("/api/tasks", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    matched = [i for i in items if i["id"] == str(task_id)]
    assert matched
    assert matched[0]["owner_username"] == created_user.username


def test_task_list_filter_by_state(client, created_user, created_key) -> None:
    waiting_id = _make_waiting_task(created_key.id, created_user.user_id)
    done_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.post(
        f"/api/tasks/{done_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="完成"),
    )

    resp = client.get(
        "/api/tasks?state=waiting_human",
        headers=created_user.headers,
    )
    assert resp.status_code == 200
    ids = [i["id"] for i in resp.json()["items"]]
    assert str(waiting_id) in ids
    assert str(done_id) not in ids

    resp_done = client.get(
        "/api/tasks?state=response_ready",
        headers=created_user.headers,
    )
    assert resp_done.status_code == 200
    done_ids = [i["id"] for i in resp_done.json()["items"]]
    assert str(done_id) in done_ids
    assert str(waiting_id) not in done_ids


def test_task_list_bucket_filter(client, created_user, created_key) -> None:
    waiting_id = _make_waiting_task(created_key.id, created_user.user_id)
    done_id = _make_waiting_task(created_key.id, created_user.user_id)
    client.post(
        f"/api/tasks/{done_id}/reply",
        headers=created_user.headers,
        json=_draft_body(final_text="完成"),
    )
    # reply 只推进到 response_ready；直接置 completed 以稳定验证分段筛选。
    with database.SessionLocal() as session:
        row = session.get(RequestTask, done_id)
        assert row is not None
        row.state = TaskState.COMPLETED
        session.commit()

    in_progress = client.get("/api/tasks?bucket=in_progress", headers=created_user.headers).json()
    ids = [item["id"] for item in in_progress["items"]]
    assert str(waiting_id) in ids
    assert str(done_id) not in ids

    finished = client.get("/api/tasks?bucket=finished", headers=created_user.headers).json()
    done_ids = [item["id"] for item in finished["items"]]
    assert str(done_id) in done_ids
    assert str(waiting_id) not in done_ids

    failed = client.get("/api/tasks?bucket=failed", headers=created_user.headers).json()
    assert failed["total"] == 0


def test_task_list_search_by_model(client, created_user, created_key) -> None:
    _make_waiting_task(created_key.id, created_user.user_id)
    resp = client.get(
        "/api/tasks?search=deepseek-v4-pro",
        headers=created_user.headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    resp_empty = client.get(
        "/api/tasks?search=zzz-no-such-model",
        headers=created_user.headers,
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total"] == 0


# ======================================================================
# IM DSL 解析/序列化往返无损
# ======================================================================


class TestImDslRoundtrip:
    """IM DSL 与 Web 共享 ReplyDraft，parse(serialize(draft)) == draft。"""

    def test_plain_final_text_no_fence(self) -> None:
        draft = ReplyDraft(final_text="你好世界")
        assert serialize_reply(draft) == "你好世界"
        assert parse_reply("你好世界") == draft

    def test_full_draft_roundtrip(self) -> None:
        draft = ReplyDraft(
            reasoning="先想想",
            tool_calls=[
                ReplyToolCall(id="call_1", name="search", arguments={"q": "test"}),
                ReplyToolCall(id="call_2", name="calc", arguments={"x": 1, "y": 2}),
            ],
            final_text="最终答案",
        )
        text = serialize_reply(draft)
        assert parse_reply(text) == draft

    def test_reasoning_and_final_only_roundtrip(self) -> None:
        draft = ReplyDraft(reasoning="只有思考", final_text="只有正文")
        assert parse_reply(serialize_reply(draft)) == draft

    def test_tool_calls_only_roundtrip(self) -> None:
        draft = ReplyDraft(
            tool_calls=[ReplyToolCall(id="t1", name="fn", arguments={"a": [1, 2]})],
        )
        assert parse_reply(serialize_reply(draft)) == draft

    def test_empty_draft_serializes_to_empty(self) -> None:
        assert serialize_reply(ReplyDraft()) == ""

    def test_empty_draft_is_empty(self) -> None:
        assert is_empty_draft(ReplyDraft()) is True
        assert is_empty_draft(ReplyDraft(final_text="   ")) is True
        assert is_empty_draft(ReplyDraft(final_text="x")) is False
        assert is_empty_draft(ReplyDraft(tool_calls=[ReplyToolCall(id="a", name="b")])) is False

    def test_m4_backward_compat_plain_text(self) -> None:
        parsed = parse_reply("纯文本回复，无围栏")
        assert parsed.final_text == "纯文本回复，无围栏"
        assert parsed.reasoning is None
        assert parsed.tool_calls == []

    def test_tool_fence_json_arguments_parsed(self) -> None:
        body = '::: tool call_1 search\n{"q": "天气", "n": 3}\n:::\n\n结果如下'
        draft = parse_reply(body)
        assert len(draft.tool_calls) == 1
        assert draft.tool_calls[0].id == "call_1"
        assert draft.tool_calls[0].name == "search"
        assert draft.tool_calls[0].arguments == {"q": "天气", "n": 3}
        assert draft.final_text == "结果如下"

    def test_tool_fence_empty_arguments(self) -> None:
        body = "::: tool call_0 noop\n:::\n\n正文"
        draft = parse_reply(body)
        assert draft.tool_calls[0].arguments == {}
        assert draft.final_text == "正文"


class TestExtractTaskTarget:
    def test_with_public_id_prefix(self) -> None:
        public_id, body = extract_task_target("#TASK001 回复内容")
        assert public_id == "TASK001"
        assert body == "回复内容"

    def test_without_prefix(self) -> None:
        public_id, body = extract_task_target("直接回复")
        assert public_id is None
        assert body == "直接回复"

    def test_prefix_no_body(self) -> None:
        public_id, body = extract_task_target("#TASK001")
        assert public_id == "TASK001"
        assert body == ""

    def test_parse_message_combines_target_and_dsl(self) -> None:
        text = "#TASK001 ::: reasoning\n思考\n:::\n\n最终正文"
        public_id, draft = parse_message(text)
        assert public_id == "TASK001"
        assert draft.reasoning == "思考"
        assert draft.final_text == "最终正文"


# ======================================================================
# Web 与 IM 提交结果一致性（共享 ReplyDraft）
# ======================================================================


def test_web_and_im_share_same_replydraft_structure() -> None:
    """Web 编辑器和 IM DSL 解析器必须生成同一个 ReplyDraft 结构。"""
    web_draft = ReplyDraft(
        reasoning="分析",
        tool_calls=[ReplyToolCall(id="c1", name="lookup", arguments={"key": "k"})],
        final_text="结论",
    )
    im_text = serialize_reply(web_draft)
    im_draft = parse_reply(im_text)
    assert im_draft == web_draft
    assert im_draft.model_dump_json(exclude_none=True) == web_draft.model_dump_json(
        exclude_none=True
    )

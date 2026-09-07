"""M14 工作台收件箱 + 乐观锁 + RequestView 投影（回复工作台改造）。

- GET /api/tasks/inbox：owner 的 waiting_human + 未读位，上限 10 无分页
- GET /api/tasks/inbox-summary：未读/待处理计数
- POST /api/tasks/{id}/seen：幂等已读
- PATCH /api/tasks/{id}/drafts/{id}：乐观锁 expected_version（409 draft_version_conflict）
- GET /api/tasks/{id}/request-view + /blocks/{block_id}：本次请求分区投影
"""

from __future__ import annotations

import json

import app.core.db as database
from app.domain.enums import InferenceProtocol
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, TaskInboxState, User
from app.services.inference_service import InferenceService


def _make_task(key_id: int, owner_user_id: int, content: str = "你好") -> int:
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": content}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    with database.SessionLocal() as session:
        task = InferenceService().create_task(
            session,
            key=session.get(ApiKey, key_id),
            owner=session.get(User, owner_user_id),
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        return task.id


def test_inbox_unread_then_seen(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)

    inbox = client.get("/api/tasks/inbox", headers=created_user.headers)
    assert inbox.status_code == 200
    body = inbox.json()
    assert body["waiting_count"] == 1
    assert body["unread_count"] == 1
    assert body["items"][0]["id"] == str(task_id)
    assert body["items"][0]["unread"] is True

    seen = client.post(f"/api/tasks/{task_id}/seen", headers=created_user.headers, json={})
    assert seen.status_code == 204

    summary = client.get("/api/tasks/inbox-summary", headers=created_user.headers)
    assert summary.json()["unread_count"] == 0
    assert summary.json()["waiting_count"] == 1

    inbox2 = client.get("/api/tasks/inbox", headers=created_user.headers)
    assert inbox2.json()["items"][0]["unread"] is False
    assert inbox2.json()["items"][0]["seen_at"]


def test_seen_updates_last_event_id(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    events = client.get(f"/api/tasks/{task_id}/events", headers=created_user.headers).json()
    last_event_id = int(events["items"][-1]["id"])
    client.post(
        f"/api/tasks/{task_id}/seen",
        headers=created_user.headers,
        json={"last_seen_event_id": last_event_id},
    )
    with database.SessionLocal() as session:
        row = session.get(TaskInboxState, task_id)
        assert row is not None
        assert row.last_seen_event_id == last_event_id


def test_inbox_owner_isolation(client, admin_headers, created_user, created_key) -> None:
    other = _make_task(created_key.id, created_user.user_id)
    admin_inbox = client.get("/api/tasks/inbox", headers=admin_headers).json()
    assert all(item["id"] != str(other) for item in admin_inbox["items"])
    assert admin_inbox["waiting_count"] == 0


def test_draft_update_version_conflict(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json={"final_text": "v1"},
    ).json()
    assert saved["version"] == 1

    conflict = client.patch(
        f"/api/tasks/{task_id}/drafts/{saved['id']}",
        headers=created_user.headers,
        json={"expected_version": 99, "final_text": "v2"},
    )
    assert conflict.status_code == 409
    assert "草稿" in conflict.json()["error"]["message"]


def test_draft_update_missing_expected_version_rejected(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    saved = client.post(
        f"/api/tasks/{task_id}/drafts",
        headers=created_user.headers,
        json={"final_text": "v1"},
    ).json()
    resp = client.patch(
        f"/api/tasks/{task_id}/drafts/{saved['id']}",
        headers=created_user.headers,
        json={"final_text": "v2"},
    )
    assert resp.status_code == 422


def test_request_view_returns_current_input(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id, content="你好，这是问题")
    resp = client.get(f"/api/tasks/{task_id}/request-view", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["task"]["id"] == str(task_id)
    assert body["raw_request_available"] is True
    assert "current_input" in body
    assert "attached_context" in body
    assert "caller_system" in body
    assert "attachments" in body
    assert "caller_tools" in body
    # 当前输入优先展示，且携带稳定 block id。
    assert body["current_input"] != [] or body["attached_context"] != []
    blocks = body["current_input"] if body["current_input"] else body["attached_context"]
    assert blocks[0]["id"].startswith("ctx_")
    assert blocks[0]["blocks"][0]["id"].startswith("blk_")


def test_request_view_block_lazy_load(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id, content="这段是完整正文内容")
    view = client.get(f"/api/tasks/{task_id}/request-view", headers=created_user.headers).json()
    items = view["current_input"] if view["current_input"] else view["attached_context"]
    block_id = items[0]["blocks"][0]["id"]
    block = client.get(
        f"/api/tasks/{task_id}/request-view/blocks/{block_id}", headers=created_user.headers
    )
    assert block.status_code == 200
    assert block.json()["type"] == "text"
    assert "完整正文内容" in block.json()["text"]


def test_request_view_missing_block_404(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    resp = client.get(
        f"/api/tasks/{task_id}/request-view/blocks/blk_nonexistent", headers=created_user.headers
    )
    assert resp.status_code == 404


def test_request_view_admin_readonly_visible(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    resp = client.get(f"/api/tasks/{task_id}/request-view", headers=admin_headers)
    assert resp.status_code == 200

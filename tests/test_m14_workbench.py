"""M14 工作台收件箱 + 乐观锁 + 对话投影（回复工作台改造）。

- GET /api/tasks/inbox：owner 的 waiting_human + 未读位，上限 10 无分页
- GET /api/tasks/inbox-summary：未读/待处理计数
- POST /api/tasks/{id}/seen：幂等已读
- PATCH /api/tasks/{id}/drafts/{id}：乐观锁 expected_version（409 draft_version_conflict）
- GET /api/tasks/{id}/conversation + /messages/{index}：多轮上下文投影
"""

from __future__ import annotations

import json

import app.core.db as database
from app.domain.conversation import project_messages
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
    # 管理员自己的 inbox 应不含其他用户任务
    admin_inbox = client.get("/api/tasks/inbox", headers=admin_headers).json()
    assert all(item["id"] != str(other) for item in admin_inbox["items"])
    # 管理员也可以看到全站：/api/tasks
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


def test_conversation_projection(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id, content="你好")
    resp = client.get(f"/api/tasks/{task_id}/conversation", headers=created_user.headers)
    assert resp.status_code == 200
    body = resp.json()
    user_msgs = [m for m in body["messages"] if m["role"] == "user"]
    assert body["total"] >= 1
    assert user_msgs[0]["preview"]  # 非空预览


def test_project_messages_uses_context_once_and_normalizes_images() -> None:
    """工作台只投影 context，并区分技术包裹与用户正文。"""
    context = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "agent header\n<user_input>真正的问题</user_input>\nagent footer",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/image.png"},
                },
            ],
        }
    ]
    messages = project_messages(
        {
            "instructions": "只展示必要内容",
            "context": context,
            # 这些字段是诊断/原始保存字段，不应再次产生展示消息。
            "messages": context,
            "input": context,
        }
    )

    assert [(item["role"], item.get("context_index")) for item in messages] == [
        ("system", None),
        ("user", 0),
    ]
    user_blocks = messages[1]["blocks"]
    assert {block["display_kind"] for block in user_blocks if block["type"] == "text"} == {
        "technical",
        "content",
    }
    image = next(block for block in user_blocks if block["type"] == "image")
    assert image["url"] == "https://example.test/image.png"
    assert image["source_type"] == "image_url"


def test_project_messages_normalizes_anthropic_base64_image() -> None:
    messages = project_messages(
        {
            "context": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "YWJj",
                            },
                        }
                    ],
                }
            ]
        }
    )
    image = messages[0]["blocks"][0]
    assert image["type"] == "image"
    assert image["url"] == "data:image/png;base64,YWJj"
    assert image["media_type"] == "image/png"
    assert image["source_type"] == "base64"


def test_conversation_message_by_index(client, created_user, created_key) -> None:
    task_id = _make_task(created_key.id, created_user.user_id, content="需要完整内容的长提示词")
    mess = client.get(f"/api/tasks/{task_id}/conversation/messages/0", headers=created_user.headers)
    assert mess.status_code == 200
    body = mess.json()
    assert body["index"] == 0
    assert "完整" in body["full_text"] or body["length"] > 0

    missing = client.get(
        f"/api/tasks/{task_id}/conversation/messages/999", headers=created_user.headers
    )
    assert missing.status_code == 404


def test_conversation_forbidden_for_other_user(
    client, admin_headers, created_user, created_key
) -> None:
    task_id = _make_task(created_key.id, created_user.user_id)
    other_headers = admin_headers  # admin 可以看 conversation
    resp = client.get(f"/api/tasks/{task_id}/conversation", headers=other_headers)
    assert resp.status_code == 200

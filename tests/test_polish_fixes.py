"""观察项修复验证：IM 事件归属、llm 策略跳过 IM 投递、前端草稿去重契约。

后端两项 + 前端 dsl.test.ts（vitest，14 例）配套。
"""

from __future__ import annotations

import json

from sqlalchemy import select

import app.core.db as database
from app.domain.enums import InferenceProtocol, TaskEventType
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, ImConnection, RequestTask, TaskEvent, User
from app.services.inference_service import InferenceService


def _make_task(
    api_key_id: int,
    user_id: int,
    *,
    delivery: str = "web",
    strategy: str = "human",
    im_connection_id: int | None = None,
    llm_config_id: int | None = None,
) -> int:
    """经编排服务创建任务；先改 Key 快照源（delivery/strategy/connection）再建任务。"""
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    from sqlalchemy import update as sa_update

    with database.SessionLocal() as session:
        session.execute(
            sa_update(ApiKey)
            .where(ApiKey.id == api_key_id)
            .values(
                delivery_mode=delivery,
                reply_strategy=strategy,
                im_connection_id=im_connection_id,
                llm_config_id=llm_config_id if strategy != "human" else None,
            )
        )
        session.commit()
        session.expire_all()
        key = session.get(ApiKey, api_key_id)
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
        return task.id


def _events(task_id: int, event_type: TaskEventType) -> list[TaskEvent]:
    with database.SessionLocal() as session:
        return list(
            session.execute(
                select(TaskEvent).where(
                    TaskEvent.task_id == task_id, TaskEvent.event_type == event_type
                )
            ).scalars()
        )


def _delivered_events(task_id: int) -> list[TaskEvent]:
    return _events(task_id, TaskEventType.DELIVERED)


def _make_connection(client, user_headers, name: str) -> int:
    resp = client.post(
        "/api/im-connections",
        headers=user_headers,
        json={
            "name": name,
            "platform": "webhook",
            "config": {
                "outbound_url": "https://example.internal/hook",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


# ----------------------------------------------------------------------
# #3 llm 策略跳过 IM 投递
# ----------------------------------------------------------------------


def _make_llm_config(client, user_headers, name: str) -> int:
    resp = client.post(
        "/api/llm-configs",
        headers=user_headers,
        json={
            "name": name,
            "protocol": "openai_chat",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk",
            "model": "gpt-4o-mini",
            "timeout_seconds": 60,
        },
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def test_llm_strategy_skips_im_delivery(client, created_user, created_key) -> None:
    """llm 策略 + IM 入口：不产生 delivered 事件（无需人工介入）。"""
    conn_id = _make_connection(client, created_user.headers, "llm-skip")
    cfg_id = _make_llm_config(client, created_user.headers, "llm-skip-cfg")
    task_id = _make_task(
        created_key.id,
        created_user.user_id,
        delivery="im",
        strategy="llm",
        im_connection_id=conn_id,
        llm_config_id=cfg_id,
    )
    assert _delivered_events(task_id) == []


def test_human_strategy_still_delivers_to_im(client, created_user, created_key) -> None:
    """human 策略 + IM 入口：_deliver 不跳过（人工等待需要通知）。"""
    from unittest.mock import patch

    conn_id = _make_connection(client, created_user.headers, "human-deliver")
    called = {"deliver": False}

    def fake_deliver(self, session, task, connection):
        called["deliver"] = True

    with patch(
        "app.services.delivery_service.DeliveryService.deliver_task",
        fake_deliver,
    ):
        _make_task(
            created_key.id,
            created_user.user_id,
            delivery="im",
            strategy="human",
            im_connection_id=conn_id,
        )
    assert called["deliver"] is True


def test_llm_strategy_skips_delivery_service_call(client, created_user, created_key) -> None:
    """llm 策略：_deliver 在触达 DeliveryService 前直接返回。"""
    from unittest.mock import patch

    conn_id = _make_connection(client, created_user.headers, "llm-skip-2")
    cfg_id = _make_llm_config(client, created_user.headers, "llm-skip-cfg-2")
    called = {"deliver": False}

    def fake_deliver(self, session, task, connection):
        called["deliver"] = True

    with patch(
        "app.services.delivery_service.DeliveryService.deliver_task",
        fake_deliver,
    ):
        _make_task(
            created_key.id,
            created_user.user_id,
            delivery="im",
            strategy="llm",
            im_connection_id=conn_id,
            llm_config_id=cfg_id,
        )
    assert called["deliver"] is False


# ----------------------------------------------------------------------
# #2 IM 回复事件 actor_user_id
# ----------------------------------------------------------------------


def test_im_reply_events_carry_owner_user_id(client, created_user, created_key) -> None:
    """经 ConnectionService._submit_task_reply 的 accepted/late 事件带归属用户。"""
    from app.services.connection_service import ConnectionService

    conn_id = _make_connection(client, created_user.headers, "actor-conn")
    task_id = _make_task(
        created_key.id,
        created_user.user_id,
        delivery="web",
        strategy="human",
    )

    class _Msg:
        text = "#none 直接回复"
        external_message_id = "msg-actor-1"
        reply_to_public_id: str | None = None

    class _Receipt:
        task_id: int | None = None
        payload_hash = "hash-1"

    with database.SessionLocal() as session:
        row = session.get(ImConnection, conn_id)
        service = ConnectionService()
        # 先造唯一等待任务语义：直接指定 public_id 定位
        task_row = session.get(RequestTask, task_id)
        msg = _Msg()
        msg.text = f"#{task_row.public_id} 最终回复内容"
        receipt = _Receipt()
        result = service._submit_task_reply(
            session,
            row=row,
            message=msg,
            receipt=receipt,  # type: ignore[arg-type]
        )
        session.commit()
        assert result.value == "accepted"
        submitted = _events(task_id, TaskEventType.REPLY_SUBMITTED)
        assert submitted, "应有 reply_submitted 事件"
        assert all(e.actor_user_id == created_user.user_id for e in submitted)

        # 再提交一次 -> late 事件同样带归属
        receipt2 = _Receipt()
        result2 = service._submit_task_reply(
            session,
            row=row,
            message=msg,
            receipt=receipt2,  # type: ignore[arg-type]
        )
        session.commit()
        assert result2.value == "late"
        late = _events(task_id, TaskEventType.REPLY_REJECTED_LATE)
        assert late, "晚到应有 reply_rejected_late 事件"
        assert all(e.actor_user_id == created_user.user_id for e in late)

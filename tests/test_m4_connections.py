"""M4 IM 连接测试：平台目录、加密配置、权限边界、绑定与进站幂等。"""

from __future__ import annotations

import secrets

import app.core.db as database
from app.core.time import utc_now
from app.domain.enums import (
    DeliveryMode,
    InboundResult,
    InferenceProtocol,
    ReplyStrategy,
    TaskState,
)
from app.repositories.models import (
    ApiKey,
    ImConnection,
    InboundReceipt,
    RequestTask,
    TaskEvent,
)


def _login(client, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_token": "test-token",
            "captcha_code": "test",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_user(client, admin_headers, username: str, password: str = "User-Pass1!") -> dict:
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={"username": username, "display_name": username, "password": password},
    )
    assert created.status_code == 201, created.text
    headers = _login(client, username, password)
    changed = client.post(
        "/api/account/password",
        headers=headers,
        json={"current_password": password, "new_password": "Changed-Pass2!"},
    )
    assert changed.status_code == 200, changed.text
    return headers


def _create_connection(
    client,
    headers,
    *,
    name: str = "webhook-conn",
    platform: str = "webhook",
    config: dict | None = None,
):
    payload = {
        "name": name,
        "platform": platform,
        "config": config
        or {
            "inbound_token": "in-token-1",
            "outbound_url": "https://example.test/hook",
            "outbound_token": "out-token-1",
        },
    }
    response = client.post("/api/im-connections", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_platform_catalog_exposes_five_platforms_with_config_schema(client, admin_headers) -> None:
    response = client.get("/api/im-platforms", headers=admin_headers)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert codes == {"wecom_ilink", "wecom_aibot", "webhook", "websocket", "http_poll"}
    websocket = next(item for item in response.json() if item["code"] == "websocket")
    assert websocket["config_schema"][0]["secret"] is True
    assert websocket["config_schema"][0]["name"] == "connection_token"


def test_connection_config_is_encrypted_and_secrets_never_echoed(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner")
    created = _create_connection(client, headers)

    # 响应不回显 Secret，只提示已设置。
    assert created["config"]["inbound_token"] is None
    assert created["config"]["inbound_token_set"] is True
    assert "in-token-1" not in response_text(created)

    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(created["id"]))
        assert row is not None
        assert "in-token-1" not in row.config_ciphertext
        assert row.config_ciphertext.startswith("hlg1.1.")


def response_text(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def test_secret_kept_when_update_submits_empty_or_omits(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-2")
    created = _create_connection(client, headers, name="keep-secret")
    connection_id = created["id"]

    omitted = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"name": "renamed"},
    )
    assert omitted.status_code == 200
    assert omitted.json()["name"] == "renamed"

    from app.services.connection_service import ConnectionService

    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == "in-token-1"

    empty = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"config": {"inbound_token": ""}},
    )
    assert empty.status_code == 200
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == "in-token-1"

    replaced = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"config": {"inbound_token": "in-token-2"}},
    )
    assert replaced.status_code == 200
    with database.SessionLocal() as session:
        row = session.get(ImConnection, int(connection_id))
        assert ConnectionService.decrypt_config(row)["inbound_token"] == "in-token-2"


def test_admin_governance_cannot_create_or_change_credentials(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-3")
    created = _create_connection(client, headers, name="governed")
    connection_id = created["id"]

    assert (
        client.post(
            "/api/im-connections",
            headers=admin_headers,
            json={"name": "admin-conn", "platform": "webhook", "config": {"inbound_token": "x"}},
        ).status_code
        == 403
    )

    listed = client.get("/api/im-connections", headers=admin_headers).json()
    assert {item["id"] for item in listed["items"]} == {connection_id}
    assert listed["items"][0]["owner_username"] == "conn-owner-3"

    forbidden = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=admin_headers,
        json={"config": {"inbound_token": "hijack"}},
    )
    assert forbidden.status_code == 403
    assert "hijack" not in forbidden.text

    # 管理员可治理启停与检查，但不能绑定或登录。
    assert (
        client.post(f"/api/im-connections/{connection_id}/start", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/im-connections/{connection_id}/health", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/im-connections/{connection_id}/stop", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/im-connections/{connection_id}/binding", headers=admin_headers
        ).status_code
        == 404
    )


def test_delete_blocked_while_enabled_api_key_references_connection(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-4")
    created = _create_connection(client, headers, name="referenced")
    connection_id = int(created["id"])

    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    with database.SessionLocal() as session:
        session.add(
            ApiKey(
                owner_user_id=int(user_id),
                name="im-key",
                key_hash="hash-1",
                key_prefix="hlg_imkey",
                delivery_mode=DeliveryMode.IM,
                im_connection_id=connection_id,
                reply_strategy=ReplyStrategy.HUMAN,
                human_timeout_seconds=300,
            )
        )
        session.commit()

    conflict = client.delete(f"/api/im-connections/{connection_id}", headers=headers)
    assert conflict.status_code == 409

    with database.SessionLocal() as session:
        session.query(ApiKey).filter(ApiKey.id == session.query(ApiKey).first().id).update(
            {"is_enabled": False}
        )
        session.commit()
    assert client.delete(f"/api/im-connections/{connection_id}", headers=headers).status_code == 204


def test_binding_code_flow_and_unbound_inbound(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-5")
    created = _create_connection(client, headers, name="binding")
    connection_id = created["id"]

    status = client.get(
        f"/api/im-connections/{connection_id}/binding/status", headers=headers
    ).json()
    assert status == {"bound": False, "binding_pending": False, "binding_expires_at": None}

    binding = client.post(f"/api/im-connections/{connection_id}/binding", headers=headers).json()
    assert binding["binding_code"]
    assert (
        client.get(f"/api/im-connections/{connection_id}/binding/status", headers=headers).json()[
            "binding_pending"
        ]
        is True
    )

    # 未绑定且无绑定码：进站按 unbound 处理。
    inbound = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "m-1", "sender": "u-1", "text": "hello"},
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert inbound.status_code == 200
    assert inbound.json()["result"] == InboundResult.UNBOUND.value

    bound = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={
            "external_message_id": "m-2",
            "sender": "u-1",
            "text": "hello",
            "binding_code": binding["binding_code"],
        },
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert bound.json()["result"] == InboundResult.BOUND.value
    assert (
        client.get(f"/api/im-connections/{connection_id}/binding/status", headers=headers).json()[
            "bound"
        ]
        is True
    )

    # 其他发送者不能回复。
    stranger = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "m-3", "sender": "u-2", "text": "hello"},
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert stranger.json()["result"] == InboundResult.UNBOUND.value


def test_webhook_inbound_is_idempotent_and_first_reply_wins(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-6")
    created = _create_connection(client, headers, name="idempotent")
    connection_id = int(created["id"])
    client.post(f"/api/im-connections/{connection_id}/binding", headers=headers)
    binding = client.post(f"/api/im-connections/{connection_id}/binding", headers=headers).json()

    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    key_id = _seed_key_and_task(user_id, connection_id)

    client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={
            "external_message_id": "bind-1",
            "sender": "u-1",
            "text": "hi",
            "binding_code": binding["binding_code"],
        },
        headers={"X-Webhook-Token": "in-token-1"},
    )

    first = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-1", "sender": "u-1", "text": "#task_public_1 第一段回复"},
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert first.json()["result"] == InboundResult.ACCEPTED.value

    duplicate = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-1", "sender": "u-1", "text": "#task_public_1 重复"},
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert duplicate.json()["result"] == InboundResult.DUPLICATE.value

    late = client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={"external_message_id": "msg-2", "sender": "u-1", "text": "#task_public_1 晚到"},
        headers={"X-Webhook-Token": "in-token-1"},
    )
    assert late.json()["result"] == InboundResult.LATE.value

    with database.SessionLocal() as session:
        task = session.get(RequestTask, key_id)
        assert task.state is TaskState.RESPONSE_READY
        assert "第一段回复" in task.response_payload_json
        receipts = {
            row.external_message_id: row.result_code
            for row in session.query(InboundReceipt).filter(
                InboundReceipt.connection_id == connection_id
            )
        }
        assert receipts["msg-1"] == InboundResult.ACCEPTED.value
        assert receipts["msg-2"] == InboundResult.LATE.value
        events = [
            row.event_type.value
            for row in session.query(TaskEvent).filter(TaskEvent.task_id == task.id)
        ]
        assert "reply_submitted" in events and "reply_rejected_late" in events


def test_webhook_inbound_requires_connection_token(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-7")
    created = _create_connection(client, headers, name="token-guard")
    connection_id = created["id"]

    assert (
        client.post(
            f"/connectors/webhook/{connection_id}/inbound",
            json={"external_message_id": "m-1", "text": "x"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/connectors/webhook/{connection_id}/inbound",
            json={"external_message_id": "m-1", "text": "x"},
            headers={"X-Webhook-Token": "wrong"},
        ).status_code
        == 401
    )


def test_http_poll_cursor_reply_and_ack_are_idempotent(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-8")
    created = _create_connection(
        client,
        headers,
        name="poller",
        platform="http_poll",
        config={"pull_token": "pull-token-1"},
    )
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_key_and_task(user_id, connection_id, public_id="task_public_poll")

    from app.services.connection_service import ConnectionService
    from app.services.delivery_service import DeliveryService

    service = ConnectionService()
    # 轮询连接器没有 webhook 入站入口，直接置位绑定身份。
    with database.SessionLocal() as session:
        service.repo.bind_external_user(session, connection_id, "u-1")
        session.commit()
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        connection = session.get(ImConnection, connection_id)
        # 未运行实例：outbox 仍应记录投递包，任务继续在 Web 可见。
        outcome = DeliveryService().deliver_task(session, task=task, connection=connection)
        session.commit()
    assert outcome.delivered is False
    assert outcome.error_code == "connection_offline"
    assert outcome.via_outbox is True

    pulled = client.get(
        f"/connectors/http/{connection_id}/tasks",
        params={"cursor": 0},
        headers={"X-Pull-Token": "pull-token-1"},
    ).json()
    assert [item["task_id"] for item in pulled["tasks"]] == ["task_public_poll"]
    assert pulled["cursor"] > 0

    # 重复 cursor 不重复投递新内容。
    second = client.get(
        f"/connectors/http/{connection_id}/tasks",
        params={"cursor": pulled["cursor"]},
        headers={"X-Pull-Token": "pull-token-1"},
    ).json()
    assert second["tasks"] == []

    reply = client.post(
        f"/connectors/http/{connection_id}/replies",
        json={
            "external_message_id": "poll-reply-1",
            "task_id": "task_public_poll",
            "text": "轮询回复",
        },
        headers={"X-Pull-Token": "pull-token-1"},
    )
    assert reply.json()["result"] == InboundResult.ACCEPTED.value

    assert client.post(
        f"/connectors/http/{connection_id}/ack",
        json={"task_id": "task_public_poll"},
        headers={"X-Pull-Token": "pull-token-1"},
    ).json() == {"acked": True}
    assert client.post(
        f"/connectors/http/{connection_id}/ack",
        json={"task_id": "task_public_poll"},
        headers={"X-Pull-Token": "pull-token-1"},
    ).json() == {"acked": False}

    with database.SessionLocal() as session:
        assert service.repo.get(session, connection_id) is not None


def _seed_key_and_task(
    owner_user_id: int, connection_id: int, public_id: str = "task_public_1"
) -> int:
    """创建 Key 与 WAITING_HUMAN 任务，返回任务 id。"""
    with database.SessionLocal() as session:
        key = ApiKey(
            owner_user_id=owner_user_id,
            name=f"key-{secrets.token_hex(4)}",
            key_hash=f"hash-{secrets.token_hex(4)}",
            key_prefix="hlg_seed",
            delivery_mode=DeliveryMode.IM,
            im_connection_id=connection_id,
            reply_strategy=ReplyStrategy.HUMAN,
            human_timeout_seconds=300,
        )
        session.add(key)
        session.flush()
        task = RequestTask(
            public_id=public_id,
            owner_user_id=owner_user_id,
            api_key_id=key.id,
            api_key_prefix_snapshot=key.key_prefix,
            requested_model="deepseek-v4-pro",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json='{"messages":[{"role":"user","content":"你好"}]}',
            normalized_request_json='{"messages":[{"role":"user","content":"你好"}],"tools":[]}',
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.IM,
            im_connection_id_snapshot=connection_id,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=utc_now(),
        )
        session.add(task)
        session.commit()
        return task.id


def test_inbound_handler_rejects_unknown_platform_config(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-9")
    response = client.post(
        "/api/im-connections",
        headers=headers,
        json={"name": "bad", "platform": "unknown", "config": {}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"

    invalid = client.post(
        "/api/im-connections",
        headers=headers,
        json={
            "name": "bad-config",
            "platform": "webhook",
            "config": {"inbound_token": "t", "outbound_url": "ftp://nope"},
        },
    )
    assert invalid.status_code == 400
    assert "http" in invalid.json()["error"]["message"]


def test_owner_isolation_for_connections(client, admin_headers) -> None:
    headers_a = _create_user(client, admin_headers, "conn-owner-a")
    headers_b = _create_user(client, admin_headers, "conn-owner-b")
    created = _create_connection(client, headers_a, name="private-conn")
    connection_id = created["id"]

    assert client.get("/api/im-connections", headers=headers_b).json()["total"] == 0
    assert client.get(f"/api/im-connections/{connection_id}", headers=headers_b).status_code == 404
    assert (
        client.delete(f"/api/im-connections/{connection_id}", headers=headers_b).status_code == 404
    )


def test_single_connection_can_be_selected_by_multiple_api_keys(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, "conn-owner-10")
    created = _create_connection(client, headers, name="shared-conn")
    connection_id = int(created["id"])

    first = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "shared-a", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    second = client.post(
        "/api/api-keys",
        headers=headers,
        json={"name": "shared-b", "delivery_mode": "im", "im_connection_id": connection_id},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    # 被任一启用 Key 引用时不允许删除连接。
    assert client.delete(f"/api/im-connections/{connection_id}", headers=headers).status_code == 409

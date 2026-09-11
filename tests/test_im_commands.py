"""IM 回复命令与外发内容测试（docs/PRODUCT.md §6.4）。

覆盖：
- DSL 命令层：/ans /res /page /file /commit 解析与未知命令；
- 进站命令路由：#id 回复、/ans、/res、/page、/file、/commit、未知命令；
- 草稿暂存与提交：/res /ans 暂存（多次覆盖保留最后一次）、/commit 确认提交；
- 两消息投递：提示条+内容条组装、预算内简化、outbox 载荷带 messages；
- /page 分页聊天记录、/file 聊天记录文件格式；
- LLM 总结开关：关闭时降级尾部摘要、配置缺失静默降级。
"""

from __future__ import annotations

import json
import secrets

import pytest

import app.core.db as database
from app.core.constants import (
    IM_CONTENT_DETAIL_CHARS,
    IM_HINT_BAR_CHARS,
    IM_PAGE_CHARS,
)
from app.core.time import utc_now
from app.domain.dsl import is_command_text, parse_command
from app.domain.enums import (
    DeliveryMode,
    DraftState,
    InboundResult,
    InferenceProtocol,
    ReplyStrategy,
    TaskState,
)
from app.repositories.models import (
    ApiKey,
    ConnectorOutbox,
    ImConnection,
    RequestTask,
    TaskDraft,
)

# ---------------------------------------------------------------------------
# DSL 命令层
# ---------------------------------------------------------------------------


def test_parse_command_slash_commands() -> None:
    command = parse_command("/ans 修复 bug")
    assert command.name == "ans"
    assert command.body == "修复 bug"
    assert parse_command("/res :::\nplain text").name == "res"
    page = parse_command("/page 2")
    assert (page.name, page.args) == ("page", "2")
    assert parse_command("/page").args == ""
    file_cmd = parse_command("/file md")
    assert (file_cmd.name, file_cmd.args) == ("file", "md")
    assert parse_command("/file").args == ""
    commit = parse_command("/commit")
    assert commit.name == "commit"
    assert commit.args == "" and commit.body == ""
    unknown = parse_command("/restart now")
    assert unknown is not None and unknown.unknown is True
    assert is_command_text("/page 1") is True
    assert is_command_text("/commit") is True
    assert is_command_text("普通文本") is False
    assert is_command_text("#task_public_1 正文") is False


def test_parse_command_target_extraction() -> None:
    """命令在前语法：/cmd #<task_public_id> 正文。"""
    ans = parse_command("/ans #task_public_cmd 修复 bug")
    assert (ans.name, ans.target, ans.body) == ("ans", "task_public_cmd", "修复 bug")
    ans_no_target = parse_command("/ans 修复 #标题 行")
    assert ans_no_target.target is None
    assert ans_no_target.body == "修复 #标题 行"
    page = parse_command("/page #task_public_cmd 2")
    assert (page.name, page.target, page.args) == ("page", "task_public_cmd", "2")
    file_cmd = parse_command("/file #task_public_cmd md")
    assert (file_cmd.name, file_cmd.target, file_cmd.args) == ("file", "task_public_cmd", "md")
    commit = parse_command("/commit #task_public_cmd")
    assert (commit.name, commit.target) == ("commit", "task_public_cmd")
    assert parse_command("/ans #task_public_cmd 正文").body == "正文"


# ---------------------------------------------------------------------------
# 进站命令路由（webhook 入站）
# ---------------------------------------------------------------------------


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


def _create_connection(client, headers, *, name="cmd-conn", platform="webhook", config=None):
    payload = {
        "name": name,
        "platform": platform,
        "config": config
        if config is not None
        else {"outbound_url": "https://example.test/hook", "outbound_token": "out-token-1"},
    }
    response = client.post("/api/im-connections", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _generated_token(created: dict, field: str = "inbound_token") -> str:
    token = (created.get("generated_tokens") or {}).get(field)
    assert token and token.startswith("hllm-")
    return token


def _seed_task(
    owner_user_id: int,
    connection_id: int,
    public_id: str,
    *,
    prompt: str = "你好",
) -> int:
    with database.SessionLocal() as session:
        key = ApiKey(
            owner_user_id=owner_user_id,
            name=f"key-{secrets.token_hex(4)}",
            key_hash=f"hash-{secrets.token_hex(4)}",
            key_prefix="sk-seed1",
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
            api_key_name_snapshot=key.name,
            requested_model="deepseek-v4-pro",
            protocol=InferenceProtocol.OPENAI_CHAT,
            raw_payload_json=json.dumps({"messages": [{"role": "user", "content": prompt}]}),
            normalized_request_json=json.dumps(
                {"messages": [{"role": "user", "content": prompt}], "tools": []},
                ensure_ascii=False,
            ),
            reply_strategy_snapshot=ReplyStrategy.HUMAN,
            delivery_mode_snapshot=DeliveryMode.IM,
            im_connection_id_snapshot=connection_id,
            state=TaskState.WAITING_HUMAN,
            slot_acquired_at=utc_now(),
        )
        session.add(task)
        session.commit()
        return task.id


def _bind(client, connection_id: int, token: str, headers, sender: str = "u-1") -> None:
    binding = client.post(f"/api/im-connections/{connection_id}/binding", headers=headers).json()
    client.post(
        f"/connectors/webhook/{connection_id}/inbound",
        json={
            "external_message_id": f"bind-{secrets.token_hex(4)}",
            "sender": sender,
            "text": "hi",
            "binding_code": binding["binding_code"],
        },
        headers={"X-Webhook-Token": token},
    )


@pytest.fixture()
def webhook_scene(client, admin_headers):
    """已绑定 webhook 连接 + 种子任务，返回常用上下文。"""
    headers = _create_user(client, admin_headers, f"cmd-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="cmd-conn")
    connection_id = int(created["id"])
    token = _generated_token(created)
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_task(user_id, connection_id, "task_public_cmd", prompt="短提问")
    _bind(client, connection_id, token, headers)
    return {
        "connection_id": connection_id,
        "token": token,
        "task_id": task_id,
        "task_public_id": "task_public_cmd",
    }


def _owner_id(client, scene: dict) -> int:
    """取 webhook_scene 中任务所有者的用户 id。"""
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        return task.owner_user_id


def _inbound(client, scene: dict, message_id: str, text: str):
    return client.post(
        f"/connectors/webhook/{scene['connection_id']}/inbound",
        json={
            "external_message_id": message_id,
            "sender": "u-1",
            "text": text,
        },
        headers={"X-Webhook-Token": scene["token"]},
    )


def test_ans_res_commands_submit_replies(client, webhook_scene) -> None:
    scene = webhook_scene

    # /ans 暂存回答：任务仍处 WAITING_HUMAN，草稿记录 final_text
    assert _inbound(client, scene, "cmd-ans-1", "/ans 命令回复").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.WAITING_HUMAN
        draft = (
            session.query(TaskDraft)
            .filter(TaskDraft.task_id == scene["task_id"], TaskDraft.state == DraftState.EDITING)
            .one()
        )
        assert "命令回复" in draft.final_text

    # /commit 确认提交：任务推进到 RESPONSE_READY，草稿标记 SUBMITTED
    assert _inbound(client, scene, "cmd-commit-1", "/commit").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.RESPONSE_READY
        assert "命令回复" in task.response_payload_json
        draft = session.query(TaskDraft).filter(TaskDraft.task_id == scene["task_id"]).one()
        assert draft.state is DraftState.SUBMITTED

    # /res 强制纯文本：fence 语法不当普通文本处理，暂存 reasoning
    scene2_task = _seed_task(_owner_id(client, scene), scene["connection_id"], "task_public_cmd2")
    assert _inbound(client, scene, "cmd-res-1", "/res :::\nplain").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene2_task)
        assert task.state is TaskState.WAITING_HUMAN
        draft = (
            session.query(TaskDraft)
            .filter(TaskDraft.task_id == scene2_task, TaskDraft.state == DraftState.EDITING)
            .one()
        )
        assert "plain" in draft.reasoning_text

    # /commit 提交含 reasoning 的草稿
    assert _inbound(client, scene, "cmd-commit-2", "/commit").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene2_task)
        assert task.state is TaskState.RESPONSE_READY
        assert "plain" in task.response_payload_json


def test_multiple_res_commands_keep_last(client, webhook_scene) -> None:
    scene = webhook_scene
    # 多次 /res：保留最后一次
    assert _inbound(client, scene, "cmd-res-a", "/res 第一版思考").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    assert _inbound(client, scene, "cmd-res-b", "/res 第二版思考").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        draft = (
            session.query(TaskDraft)
            .filter(TaskDraft.task_id == scene["task_id"], TaskDraft.state == DraftState.EDITING)
            .one()
        )
        assert draft.reasoning_text == "第二版思考"
        assert "第一版思考" not in draft.reasoning_text


def test_commit_without_draft_returns_unhandled(client, webhook_scene) -> None:
    scene = webhook_scene
    # 无暂存草稿直接 /commit：UNHANDLED，任务保持 WAITING_HUMAN
    assert _inbound(client, scene, "cmd-commit-none", "/commit").json()["result"] == (
        InboundResult.UNHANDLED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.WAITING_HUMAN


def test_page_file_unknown_commands(client, webhook_scene) -> None:
    scene = webhook_scene

    # /page 入队 outbox 平台载荷（webhook 属 outbox 平台，尽力推送失败忽略）
    assert _inbound(client, scene, "cmd-page-1", "/page 1").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        rows = (
            session.query(ConnectorOutbox)
            .filter(ConnectorOutbox.connection_id == scene["connection_id"])
            .all()
        )
        assert rows, "outbox 应有 /page 载荷"
        payloads = [json.loads(row.payload_json) for row in rows]
        page_payload = next((p for p in payloads if p.get("kind") == "page"), None)
        assert page_payload is not None
        assert "task_public_cmd" in page_payload.get("text", "")
        assert "共 " in page_payload["text"] and " 页，当前第 1 页" in page_payload["text"]

    # /file txt：整个聊天记录（用户:/助手: 标注）
    assert _inbound(client, scene, "cmd-file-1", "/file txt").json()["result"] == (
        InboundResult.ACCEPTED.value
    )
    with database.SessionLocal() as session:
        rows = (
            session.query(ConnectorOutbox)
            .filter(ConnectorOutbox.connection_id == scene["connection_id"])
            .all()
        )
        payloads = [json.loads(row.payload_json) for row in rows]
        assert any(
            p.get("kind") == "file" and p.get("filename") == "task_public_cmd.txt" for p in payloads
        )
        file_payload = next((p for p in payloads if p.get("kind") == "file"), None)
        assert file_payload is not None
        assert "用户: " in file_payload.get("content", "")
        assert "短提问" in file_payload.get("content", "")

    # 未知命令不作为回复提交
    assert _inbound(client, scene, "cmd-unknown-1", "/restart now").json()["result"] == (
        InboundResult.UNHANDLED.value
    )
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.WAITING_HUMAN


def test_ans_to_late_task_returns_late(client, webhook_scene) -> None:
    scene = webhook_scene
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        task.state = TaskState.RESPONSE_READY
        session.commit()
    # 显式引用任务 /ans 暂存草稿（任务已完成，暂存仍被接受）
    assert _inbound(client, scene, "cmd-ans-late", "#task_public_cmd /ans 太晚了").json()[
        "result"
    ] == (InboundResult.ACCEPTED.value)
    # /commit 提交到已完成任务：first_reply_wins 返回 False -> LATE
    assert _inbound(client, scene, "cmd-commit-late", "#task_public_cmd /commit").json()[
        "result"
    ] == (InboundResult.LATE.value)


def test_command_first_target_syntax(client, webhook_scene) -> None:
    """命令在前语法 /cmd #id ...：两种语法等价，均按 #id 定位任务。"""
    scene = webhook_scene

    # /ans #id 暂存草稿（等价于 #id /ans）
    assert _inbound(client, scene, "cmd-first-ans", "/ans #task_public_cmd 新语法回复").json()[
        "result"
    ] == (InboundResult.ACCEPTED.value)
    with database.SessionLocal() as session:
        draft = (
            session.query(TaskDraft)
            .filter(TaskDraft.task_id == scene["task_id"], TaskDraft.state == DraftState.EDITING)
            .one()
        )
        assert draft.final_text == "新语法回复"
        assert "#task_public_cmd" not in draft.final_text

    # /commit #id 提交草稿
    assert _inbound(client, scene, "cmd-first-commit", "/commit #task_public_cmd").json()[
        "result"
    ] == (InboundResult.ACCEPTED.value)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene["task_id"])
        assert task.state is TaskState.RESPONSE_READY
        assert "新语法回复" in task.response_payload_json

    # /page #id 指定任务分页外发
    assert _inbound(client, scene, "cmd-first-page", "/page #task_public_cmd 1").json()[
        "result"
    ] == (InboundResult.ACCEPTED.value)
    with database.SessionLocal() as session:
        payloads = [
            json.loads(row.payload_json)
            for row in session.query(ConnectorOutbox)
            .filter(ConnectorOutbox.connection_id == scene["connection_id"])
            .all()
        ]
    page_payload = next((p for p in payloads if p.get("kind") == "page"), None)
    assert page_payload is not None
    assert "task_public_cmd" in page_payload.get("text", "")

    # 显式 #id 不存在：拒绝且不落到唯一等待任务（避免误回复别的任务）
    scene2_task = _seed_task(_owner_id(client, scene), scene["connection_id"], "task_public_cmd3")
    assert _inbound(client, scene, "cmd-first-bad", "/ans #task_missing 不该提交").json()[
        "result"
    ] == (InboundResult.UNHANDLED.value)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, scene2_task)
        assert task.state is TaskState.WAITING_HUMAN


def test_command_first_legacy_syntax_pure_text(client, webhook_scene) -> None:
    """任务在前语法 #id /ans ...：剥掉定位后命令仍被识别。"""
    scene = webhook_scene
    assert _inbound(client, scene, "cmd-legacy-ans", "#task_public_cmd /ans 兼容语法").json()[
        "result"
    ] == (InboundResult.ACCEPTED.value)
    with database.SessionLocal() as session:
        draft = (
            session.query(TaskDraft)
            .filter(TaskDraft.task_id == scene["task_id"], TaskDraft.state == DraftState.EDITING)
            .one()
        )
        assert draft.final_text == "兼容语法"


# ---------------------------------------------------------------------------
# 两消息投递
# ---------------------------------------------------------------------------


def test_delivery_envelope_two_messages_and_outbox_payload(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, f"deliv-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="deliv-conn")
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    long_prompt = "任务细节。" * 200  # ~1000 字，超内容条预算
    task_id = _seed_task(user_id, connection_id, "task_public_two", prompt=long_prompt)

    from app.services.delivery_service import DeliveryService

    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        connection = session.get(ImConnection, connection_id)
        outcome = DeliveryService().deliver_task(session, task=task, connection=connection)
        session.commit()

    assert outcome.via_outbox is True
    assert outcome.error_code == "connection_offline"

    with database.SessionLocal() as session:
        row = (
            session.query(ConnectorOutbox)
            .filter(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
            )
            .one()
        )
        payload = json.loads(row.payload_json)
        messages = payload["messages"]
        assert len(messages) == 2
        hint, content = messages
        assert hint.startswith("[任务 task_public_two]")
        assert "/page" in hint
        assert len(hint) <= IM_HINT_BAR_CHARS
        assert "…（前面内容已省略" in content or len(content) <= IM_CONTENT_DETAIL_CHARS


def test_delivery_short_prompt_single_content_message(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, f"deliv-owner2-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="deliv-conn2")
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_task(user_id, connection_id, "task_public_short", prompt="你好")

    from app.services.delivery_service import DeliveryService

    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        connection = session.get(ImConnection, connection_id)
        DeliveryService().deliver_task(session, task=task, connection=connection)
        session.commit()

    with database.SessionLocal() as session:
        row = (
            session.query(ConnectorOutbox)
            .filter(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
            )
            .one()
        )
        messages = json.loads(row.payload_json)["messages"]
        assert len(messages) == 2  # 提示条 + 内容条（短 prompt 全文）
        assert "你好" in messages[1]


def test_page_splits_long_prompt(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, f"page-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="page-conn")
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    long_prompt = "字" * (IM_PAGE_CHARS * 2 + 100)
    _seed_task(user_id, connection_id, "task_public_page", prompt=long_prompt)

    from app.services.outbound_service import _split_pages

    pages = _split_pages(long_prompt, IM_PAGE_CHARS)
    assert all(len(page) <= IM_PAGE_CHARS for page in pages)
    assert sum(len(page) for page in pages) >= len(long_prompt) - 10


# ---------------------------------------------------------------------------
# LLM 总结开关
# ---------------------------------------------------------------------------


def test_connection_summary_switch_updates_and_validation(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, f"sum-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="sum-conn")
    connection_id = created["id"]

    # 默认关闭
    view = client.get(f"/api/im-connections/{connection_id}", headers=headers).json()
    assert view["llm_summary_enabled"] is False
    assert view["llm_config_id"] is None

    # 打开开关（未绑配置：允许，投递时静默降级）
    updated = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"llm_summary_enabled": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["llm_summary_enabled"] is True

    # 非法 llm_config_id（不存在）：404
    bad = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"llm_summary_enabled": True, "llm_config_id": "999999"},
    )
    assert bad.status_code == 404

    # 清空配置同时关开关
    cleared = client.patch(
        f"/api/im-connections/{connection_id}",
        headers=headers,
        json={"llm_config_id": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["llm_summary_enabled"] is False


def test_summarize_task_prompt_degrades_silently(client, admin_headers) -> None:
    import asyncio

    headers = _create_user(client, admin_headers, f"sumgen-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="sumgen-conn")
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_task(user_id, connection_id, "task_public_sum", prompt="总结测试提问")

    from app.services.llm_summary_service import summarize_task_prompt

    # 开关未开：直接 None
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        connection = session.get(ImConnection, connection_id)
        assert asyncio.run(summarize_task_prompt(session, connection=connection, task=task)) is None

        # 开关开了但无配置：None
        connection.llm_summary_enabled = True
        assert asyncio.run(summarize_task_prompt(session, connection=connection, task=task)) is None

        # 配置不存在：None（session.get 返回 None）
        connection.llm_config_id = 999999
        assert asyncio.run(summarize_task_prompt(session, connection=connection, task=task)) is None


def test_hint_bar_uses_llm_summary_text(client, admin_headers) -> None:
    headers = _create_user(client, admin_headers, f"hint-owner-{secrets.token_hex(3)}")
    created = _create_connection(client, headers, name="hint-conn")
    connection_id = int(created["id"])
    user_id = int(client.get("/api/auth/me", headers=headers).json()["id"])
    task_id = _seed_task(user_id, connection_id, "task_public_hint", prompt="原始长提问")

    from app.services.delivery_service import DeliveryService

    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        envelope = DeliveryService().build_envelope(task, summary="这是 LLM 总结")
        assert "这是 LLM 总结" in envelope.messages[0]
        assert "原始长提问" not in envelope.messages[0]

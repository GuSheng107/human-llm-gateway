"""部署运行时：任务恢复、关闭与低基数监控。"""

from __future__ import annotations

import json

import app.core.db as database
from app.domain.enums import InferenceProtocol, TaskState
from app.protocols.chat_completions import parse_request
from app.repositories.models import ApiKey, RequestTask, User
from app.services.inference_service import InferenceService
from app.services.task_lifecycle import cancel_active_tasks


def test_shutdown_cleanup_is_idempotent_and_preserves_completed_tasks(
    client, created_user, created_key
) -> None:
    raw = json.dumps(
        {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()
    with database.SessionLocal() as session:
        service = InferenceService()
        tasks = [
            service.create_task(
                session,
                key=session.get(ApiKey, created_key.id),
                owner=session.get(User, created_user.user_id),
                protocol=InferenceProtocol.OPENAI_CHAT,
                parsed=parse_request(raw),
                raw_body=raw,
                headers={},
            )
            for _ in range(2)
        ]
        service.finalize(session, tasks[1], TaskState.COMPLETED)
        session.commit()
        waiting_id, completed_id = (task.id for task in tasks)
    assert cancel_active_tasks("server_shutdown") == 1
    assert cancel_active_tasks("server_restart") == 0
    with database.SessionLocal() as session:
        assert session.get(User, created_user.user_id).active_task_count == 0
        cancelled = session.get(RequestTask, waiting_id)
        assert cancelled.state is TaskState.CANCELLED
        assert cancelled.cancel_reason_code == "server_shutdown"
        assert session.get(RequestTask, completed_id).state is TaskState.COMPLETED


def test_metrics_never_labels_resource_ids_or_secrets(client) -> None:
    client.get("/api/tasks/private-task-123", headers={"Authorization": "Bearer private-secret"})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert 'interface="management",status_class="4xx"' in response.text
    assert 'hlg_http_inflight_requests{interface="management"} 0' in response.text
    assert "private-task-123" not in response.text
    assert "private-secret" not in response.text

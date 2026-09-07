"""RequestView 服务：加载任务并组装请求视图、block 按需加载与 ctx 映射。

管理员可只读查看其权限范围内任务的请求视图；确认警告、生成草稿、
提交回复仍仅限任务所有者（§7.1）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..domain.caller_tools import CallerToolCatalog
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.request_view import (
    find_block,
    media_block_present,
    project_request_view,
    protocol_kind_of,
    public_view,
    summarize_attachments,
)
from ..repositories.models import RequestTask
from ..repositories.tasks import TaskRepository
from .caller_tool_service import catalog_for_task


class RequestViewService:
    def __init__(self) -> None:
        self.repo = TaskRepository()

    # ------------------------------------------------------------------
    # 请求视图
    # ------------------------------------------------------------------

    def build_view(self, session: Session, task: RequestTask) -> dict[str, Any]:
        """组装完整 RequestView（内部含 block 注册表；输出经 public_view 剥离）。"""
        normalized = self._normalized(task)
        kind = protocol_kind_of(task.protocol)
        view = project_request_view(kind, normalized)
        catalog = catalog_for_task(task)
        inbox = self.repo.get_inbox_state(session, task_id=task.id)
        acknowledged_at = inbox.tool_call_warning_acknowledged_at if inbox is not None else None
        view["caller_tools"] = _catalog_view(catalog)
        view["tool_call_warning"] = {
            "required": bool(catalog.definitions) and acknowledged_at is None,
            "acknowledged": acknowledged_at is not None,
            "acknowledged_at": acknowledged_at.isoformat() if acknowledged_at else None,
        }
        view["attachments"] = summarize_attachments(view)
        view["raw_request_available"] = True
        view["_task"] = task
        return view

    def get_request_view(self, session: Session, task: RequestTask) -> dict[str, Any]:
        view = self.build_view(session, task)
        task = view.pop("_task")
        return {
            "task": {
                "id": str(task.id),
                "public_id": task.public_id,
                "request_id": task.request_id,
                "protocol": task.protocol.value,
                "requested_model": task.requested_model,
                "state": task.state.value,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "deadline_at": (
                    task.human_deadline_at.isoformat() if task.human_deadline_at else None
                ),
            },
            **public_view(view),
            "attachments": view["attachments"],
            "caller_tools": view["caller_tools"],
            "tool_call_warning": view["tool_call_warning"],
            "raw_request_available": view["raw_request_available"],
        }

    def get_block(self, session: Session, task: RequestTask, block_id: str) -> dict[str, Any]:
        """按需返回附件或超长内容块的完整内容。"""
        view = self.build_view(session, task)
        block = find_block(view, block_id)
        if block is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "内容块不存在", status_code=404)
        payload: dict[str, Any] = {"id": block.get("id"), "type": block.get("type")}
        if block.get("type") == "text":
            payload["text"] = block.get("text")
        elif block.get("type") == "tool_result":
            payload["call_id"] = block.get("call_id")
            payload["text"] = block.get("text")
        elif block.get("type") == "tool_call":
            payload["name"] = block.get("name")
            payload["call_id"] = block.get("call_id")
            payload["arguments"] = block.get("arguments")
        else:
            payload.update(
                {
                    "media_type": block.get("media_type"),
                    "filename": block.get("filename"),
                    "source": block.get("source"),
                    "url": block.get("url"),
                    "size_bytes": block.get("size_bytes"),
                    "previewable": bool(block.get("previewable")),
                }
            )
            data = block.get("data")
            if data:
                payload["data_base64"] = data
        return payload

    def resolve_excluded_context_ids(
        self, task: RequestTask, excluded_context_item_ids: list[str] | None
    ) -> list[int] | None:
        """把稳定 ctx ID 解析为 normalized.context 下标（生成过滤用）。

        current_input 不可排除；未知 ID 返回 400 invalid_context_item。
        """
        if not excluded_context_item_ids:
            return None
        normalized = self._normalized(task)
        context = normalized.get("context")
        context_length = len(context) if isinstance(context, list) else 0
        kind = protocol_kind_of(task.protocol)
        view = project_request_view(kind, normalized)
        attached_map: dict[str, int] = view.get("_attached_map", {})
        indices: list[int] = []
        for item_id in excluded_context_item_ids:
            if not isinstance(item_id, str) or item_id not in attached_map:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"无效的上下文条目 ID: {item_id}",
                    status_code=400,
                    public_code="invalid_context_item",
                )
            index = attached_map[item_id]
            if index >= context_length:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"无效的上下文条目 ID: {item_id}",
                    status_code=400,
                    public_code="invalid_context_item",
                )
            indices.append(index)
        return indices

    def attachments_present(self, task: RequestTask) -> bool:
        normalized = self._normalized(task)
        kind = protocol_kind_of(task.protocol)
        view = project_request_view(kind, normalized)
        return media_block_present(view.get("_all_blocks", []))

    # ------------------------------------------------------------------

    @staticmethod
    def _normalized(task: RequestTask) -> dict[str, Any]:
        try:
            normalized = json.loads(task.normalized_request_json or "{}")
        except (ValueError, TypeError):
            normalized = {}
        return normalized if isinstance(normalized, dict) else {}


def _catalog_view(catalog: CallerToolCatalog) -> dict[str, Any]:
    return {
        "definitions": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source_type": tool.source_type,
                "is_generatable": tool.is_generatable,
            }
            for tool in catalog.definitions
        ],
        "choice": catalog.policy.choice.value,
        "required_name": catalog.policy.required_name,
        "parallel_allowed": catalog.policy.parallel_allowed,
    }

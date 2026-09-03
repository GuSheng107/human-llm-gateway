"""MCP Tool 注册表与 handler 实现。

每个 tool 声明 name/description/inputSchema/handler，handler 接收
(db_session, user, arguments) 并返回 MCP CallToolResult 格式的 dict。
只暴露只读查询操作，不暴露任何写/删除/提交操作。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ...domain.enums import UserRole
from ...repositories.api_keys import ApiKeyRepository
from ...repositories.catalog import FakeModelRepository
from ...repositories.connections import ConnectionRepository
from ...repositories.llm_configs import LlmConfigRepository
from ...repositories.models import User
from ...repositories.tasks import TaskRepository

# ---------------------------------------------------------------------------
# Tool 定义类型
# ---------------------------------------------------------------------------

McpToolHandler = Callable[[Session, User, dict[str, Any]], dict[str, Any]]


class McpToolDef:
    """MCP 工具定义。"""

    __slots__ = ("description", "handler", "input_schema", "name")

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: McpToolHandler,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_mcp_spec(self) -> dict[str, Any]:
        """输出 MCP tools/list 所需的 tool spec。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------


def _handle_list_tasks(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查询当前用户的任务列表。"""
    page = int(args.get("page", 1))
    page_size = min(int(args.get("page_size", 20)), 100)
    search = args.get("search")
    state = args.get("state")

    from ...domain.enums import TaskState

    state_enum = None
    if state:
        try:
            state_enum = TaskState(state)
        except ValueError:
            pass

    owner_filter = None if user.role is UserRole.ADMIN else user.id
    repo = TaskRepository()
    tasks, total = repo.list_page(
        session,
        page=page,
        page_size=page_size,
        owner_user_id=owner_filter,
        search=search,
        state=state_enum,
    )

    items = []
    for t in tasks:
        items.append(
            {
                "id": t.id,
                "public_id": t.public_id,
                "state": t.state.value if t.state else None,
                "requested_model": t.requested_model,
                "owner_user_id": t.owner_user_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"tasks": items, "total": total, "page": page, "page_size": page_size},
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _handle_get_task_detail(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查看任务详情（含对话历史摘要）。"""
    task_id = int(args["task_id"])
    repo = TaskRepository()
    task = repo.get(session, task_id)
    if task is None or (user.role is not UserRole.ADMIN and task.owner_user_id != user.id):
        return {
            "content": [{"type": "text", "text": "任务不存在或无权访问"}],
            "isError": True,
        }

    # 取最近事件作为对话摘要
    events, _ = repo.list_events(session, task_id=task.id, page=1, page_size=10)
    event_summaries = []
    for e in events:
        event_summaries.append(
            {
                "type": e.event_type.value if e.event_type else None,
                "actor": e.actor_type.value if e.actor_type else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
        )

    detail = {
        "id": task.id,
        "public_id": task.public_id,
        "state": task.state.value if task.state else None,
        "requested_model": task.requested_model,
        "owner_user_id": task.owner_user_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "recent_events": event_summaries,
    }

    return {
        "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}],
        "isError": False,
    }


def _handle_list_connections(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查看当前用户的 IM 连接状态。"""
    page = int(args.get("page", 1))
    page_size = min(int(args.get("page_size", 20)), 100)
    owner_filter = None if user.role is UserRole.ADMIN else user.id
    repo = ConnectionRepository()
    connections, total = repo.list_page(
        session, page=page, page_size=page_size, owner_user_id=owner_filter
    )

    items = []
    for c in connections:
        items.append(
            {
                "id": c.id,
                "platform": c.platform,
                "state": c.state.value if c.state else None,
                "owner_user_id": c.owner_user_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"connections": items, "total": total},
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _handle_list_api_keys(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查看当前用户的 API Key 列表（不含密钥明文）。"""
    page = int(args.get("page", 1))
    page_size = min(int(args.get("page_size", 20)), 100)
    owner_filter = None if user.role is UserRole.ADMIN else user.id
    repo = ApiKeyRepository()
    keys, total = repo.list_page(
        session, page=page, page_size=page_size, owner_user_id=owner_filter
    )

    items = []
    for k in keys:
        items.append(
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "is_enabled": k.is_enabled,
                "owner_user_id": k.owner_user_id,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"api_keys": items, "total": total},
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _handle_list_llm_configs(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查看当前用户的 LLM 配置列表（不含密钥明文）。"""
    page = int(args.get("page", 1))
    page_size = min(int(args.get("page_size", 20)), 100)
    owner_filter = None if user.role is UserRole.ADMIN else user.id
    repo = LlmConfigRepository()
    configs, total = repo.list_page(
        session, page=page, page_size=page_size, owner_user_id=owner_filter
    )

    items = []
    for c in configs:
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "protocol": c.protocol.value if c.protocol else None,
                "base_url": c.base_url,
                "real_model": c.real_model,
                "is_enabled": c.is_enabled,
                "has_secret": bool(c.secret_encrypted),
                "owner_user_id": c.owner_user_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"llm_configs": items, "total": total},
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _handle_list_models(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """查看可用模型列表。"""
    repo = FakeModelRepository()
    # 使用可见集合查询（系统模型 + 用户私有模型）
    models = repo.visible_models(session, user.id, only_enabled=True)

    items = []
    for m in models:
        items.append(
            {
                "id": m.id,
                "model_id": m.model_id,
                "display_name": m.display_name,
                "scope": m.scope.value if m.scope else None,
                "is_enabled": m.is_enabled,
            }
        )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"models": items, "total": len(items)},
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }


def _handle_get_system_status(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """获取系统运行状态摘要。"""
    from sqlalchemy import func, select

    from ...domain.enums import ConnectionState, TaskState
    from ...repositories.models import ApiKey, ImConnection, LlmConfig, RequestTask

    # 待回复任务数
    pending_tasks = (
        session.scalar(
            select(func.count())
            .select_from(RequestTask)
            .where(
                RequestTask.state == TaskState.WAITING_HUMAN,
                *(
                    [RequestTask.owner_user_id == user.id]
                    if user.role is not UserRole.ADMIN
                    else []
                ),
            )
        )
        or 0
    )

    # 活跃连接数
    active_connections = (
        session.scalar(
            select(func.count())
            .select_from(ImConnection)
            .where(
                ImConnection.state == ConnectionState.RUNNING,
                *(
                    [ImConnection.owner_user_id == user.id]
                    if user.role is not UserRole.ADMIN
                    else []
                ),
            )
        )
        or 0
    )

    # API Key 数
    api_key_count = (
        session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.is_enabled.is_(True),
                *([ApiKey.owner_user_id == user.id] if user.role is not UserRole.ADMIN else []),
            )
        )
        or 0
    )

    # LLM 配置数
    llm_config_count = (
        session.scalar(
            select(func.count())
            .select_from(LlmConfig)
            .where(
                LlmConfig.is_enabled.is_(True),
                *([LlmConfig.owner_user_id == user.id] if user.role is not UserRole.ADMIN else []),
            )
        )
        or 0
    )

    status = {
        "pending_tasks": pending_tasks,
        "active_connections": active_connections,
        "enabled_api_keys": api_key_count,
        "enabled_llm_configs": llm_config_count,
    }

    return {
        "content": [{"type": "text", "text": json.dumps(status, ensure_ascii=False)}],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# Tool 注册表
# ---------------------------------------------------------------------------

_TOOLS: list[McpToolDef] = [
    McpToolDef(
        name="list_tasks",
        description="查询当前用户的任务列表。支持按状态筛选和关键词搜索。",
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，默认1"},
                "page_size": {"type": "integer", "description": "每页条数，默认20，最大100"},
                "search": {
                    "type": "string",
                    "description": "搜索关键词（匹配模型名、public_id等）",
                },
                "state": {
                    "type": "string",
                    "description": "任务状态筛选",
                    "enum": [
                        "waiting_human",
                        "response_ready",
                        "forwarding_llm",
                        "forwarding_im",
                        "delivered",
                        "failed",
                        "cancelled",
                        "expired",
                    ],
                },
            },
        },
        handler=_handle_list_tasks,
    ),
    McpToolDef(
        name="get_task_detail",
        description="查看指定任务的详细信息，包括状态、模型、事件历史等。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务ID"},
            },
            "required": ["task_id"],
        },
        handler=_handle_get_task_detail,
    ),
    McpToolDef(
        name="list_connections",
        description="查看当前用户的 IM 连接列表及其状态。",
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，默认1"},
                "page_size": {"type": "integer", "description": "每页条数，默认20"},
            },
        },
        handler=_handle_list_connections,
    ),
    McpToolDef(
        name="list_api_keys",
        description="查看当前用户的 API Key 列表（不含密钥明文）。",
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，默认1"},
                "page_size": {"type": "integer", "description": "每页条数，默认20"},
            },
        },
        handler=_handle_list_api_keys,
    ),
    McpToolDef(
        name="list_llm_configs",
        description="查看当前用户的 LLM 配置列表（不含密钥明文）。",
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，默认1"},
                "page_size": {"type": "integer", "description": "每页条数，默认20"},
            },
        },
        handler=_handle_list_llm_configs,
    ),
    McpToolDef(
        name="list_models",
        description="查看当前用户可用的模型列表（Fake Model 目录）。",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=_handle_list_models,
    ),
    McpToolDef(
        name="get_system_status",
        description="获取系统运行状态摘要：待回复任务数、活跃连接数、API Key数和LLM配置数。",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=_handle_get_system_status,
    ),
]

# name -> McpToolDef 快速查找
_TOOL_REGISTRY: dict[str, McpToolDef] = {t.name: t for t in _TOOLS}


def list_mcp_tools() -> list[dict[str, Any]]:
    """返回所有已注册工具的 MCP spec 列表。"""
    return [t.to_mcp_spec() for t in _TOOLS]


def get_mcp_tool(name: str) -> McpToolDef | None:
    """按名称查找工具定义。"""
    return _TOOL_REGISTRY.get(name)


def list_openai_tools() -> list[dict[str, Any]]:
    """返回 OpenAI function calling 格式的 tools 列表（供注入上游请求）。"""
    tools = []
    for t in _TOOLS:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
        )
    return tools

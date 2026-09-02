"""工具沙箱 API（M12，docs/ROADMAP.md M12）。

- 管理员：白名单 CRUD（/api/tools）。
- 所有登录用户：执行白名单内已启用工具（POST /api/tools/{id}/execute，
  confirmed=True 由前端确认弹窗承载）与查看自己的执行历史。
- 调用方/上游声明的 tool call 永不进入本路径（协议层只做数据转发）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import ToolExecution, ToolWhitelist, User
from ..services.tools.service import ToolService
from .common import StrictModel
from .deps import require_admin, require_current_user

router = APIRouter(prefix="/api/tools", tags=["tools"])

_service = ToolService()


class ArgumentProperty(StrictModel):
    type: str = Field(pattern="^string$")
    description: str | None = None


class ArgumentsSchema(StrictModel):
    type: str = Field(pattern="^object$")
    properties: dict[str, ArgumentProperty]
    required: list[str] | None = None


class ToolCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    command_template: str = Field(min_length=1, max_length=2000)
    arguments_schema: ArgumentsSchema
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    stdin_parameter: str | None = Field(default=None, max_length=64)


class ToolUpdate(StrictModel):
    description: str | None = Field(default=None, max_length=500)
    command_template: str | None = Field(default=None, min_length=1, max_length=2000)
    arguments_schema: ArgumentsSchema | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    is_enabled: bool | None = None
    stdin_parameter: str | None = None


class ToolExecute(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class ToolView(BaseModel):
    id: str
    name: str
    description: str | None
    command_template: str | None  # 用户视图不返回（管理员可见）
    arguments_schema: dict[str, Any] | None
    stdin_parameter: str | None
    timeout_seconds: int
    is_enabled: bool
    created_at: str

    @classmethod
    def from_row(cls, row: ToolWhitelist, *, admin_view: bool) -> ToolView:
        # 参数 Schema 是调用工具所必需的公开契约；只有命令模板属于管理员机密。
        # 普通用户拿不到 Schema 会导致参数化工具在前端根本无法填写。
        try:
            schema = json.loads(row.arguments_schema_json)
        except (ValueError, TypeError):
            schema = None
        return cls(
            id=str(row.id),
            name=row.name,
            description=row.description,
            command_template=row.command_template if admin_view else None,
            arguments_schema=schema,
            stdin_parameter=row.stdin_parameter,
            timeout_seconds=row.timeout_seconds,
            is_enabled=row.is_enabled,
            created_at=iso_utc(row.created_at) or "",
        )


class ToolPage(BaseModel):
    items: list[ToolView]
    page: int
    page_size: int
    total: int


class ExecutionView(BaseModel):
    id: str
    tool_id: str
    tool_name: str
    state: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    error_code: str | None
    duration_ms: int | None
    created_at: str


class ExecutionPage(BaseModel):
    items: list[ExecutionView]
    page: int
    page_size: int
    total: int


class ExecuteResultView(ExecutionView):
    pass


def _get_tool(db: Session, tool_id: int) -> ToolWhitelist:
    row = db.get(ToolWhitelist, tool_id)
    if row is None:
        raise DomainError(DomainErrorCode.NOT_FOUND, "工具不存在", status_code=404)
    return row


@router.get("", response_model=ToolPage)
def list_tools(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    enabled_only: bool = Query(default=False),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ToolPage:
    is_admin = user.role is UserRole.ADMIN
    rows = (
        _service.list_whitelist(db, include_disabled=not enabled_only)
        if is_admin
        else _service.list_enabled_for_user(db)
    )
    total = len(rows)
    start = (page - 1) * page_size
    return ToolPage(
        items=[
            ToolView.from_row(row, admin_view=is_admin) for row in rows[start : start + page_size]
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=ToolView, status_code=201)
def create_tool(
    payload: ToolCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ToolView:
    row = _service.create(
        db,
        actor=admin,
        name=payload.name,
        description=payload.description,
        command_template=payload.command_template,
        arguments_schema=payload.arguments_schema.model_dump(),
        timeout_seconds=payload.timeout_seconds,
        stdin_parameter=payload.stdin_parameter,
    )
    db.commit()
    db.refresh(row)
    return ToolView.from_row(row, admin_view=True)


@router.patch("/{tool_id}", response_model=ToolView)
def update_tool(
    tool_id: int,
    payload: ToolUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ToolView:
    row = _get_tool(db, tool_id)
    fields = payload.model_dump(include=payload.model_fields_set)
    if "arguments_schema" in fields and fields["arguments_schema"] is not None:
        fields["arguments_schema"] = dict(fields["arguments_schema"])
    updated = _service.update(db, actor=admin, row=row, fields=fields)
    db.commit()
    db.refresh(updated)
    return ToolView.from_row(updated, admin_view=True)


@router.delete("/{tool_id}", status_code=204)
def delete_tool(
    tool_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = _get_tool(db, tool_id)
    _service.delete(db, actor=admin, row=row)
    db.commit()
    return Response(status_code=204)


@router.post("/{tool_id}/execute", response_model=ExecuteResultView, status_code=201)
def execute_tool(
    tool_id: int,
    payload: ToolExecute,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ExecuteResultView:
    """执行白名单工具：confirmed 由前端确认弹窗设置（显式确认语义）。

    confirmed=False 的拒绝（含审计）由服务层统一处理。
    """
    row = _service.execute(
        db,
        user=user,
        tool_id=tool_id,
        arguments=payload.arguments,
        confirmed=payload.confirmed,
    )
    db.commit()
    db.refresh(row)
    return _execution_view(db, row)


@router.get("/executions", response_model=ExecutionPage)
def list_executions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ExecutionPage:
    rows, total = _service.list_executions(db, user=user, page=page, page_size=page_size)
    return ExecutionPage(
        items=[_execution_view(db, row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


def _execution_view(db: Session, row: ToolExecution) -> ExecutionView:
    tool = db.get(ToolWhitelist, row.tool_id)
    return ExecutionView(
        id=str(row.id),
        tool_id=str(row.tool_id),
        tool_name=tool.name if tool else "-",
        state=row.state.value,
        exit_code=row.exit_code,
        stdout=row.stdout_text,
        stderr=row.stderr_text,
        error_code=row.error_code,
        duration_ms=row.duration_ms,
        created_at=iso_utc(row.created_at) or "",
    )

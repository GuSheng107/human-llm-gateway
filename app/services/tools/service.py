"""工具白名单与执行用例（M12）。

边界（docs/ROADMAP.md M12）：
- 管理员维护白名单；用户只能执行白名单内已启用工具。
- 执行必须显式发起（用户点击 + 前端确认弹窗承载"显式确认"语义，
  confirmed=True 由前端确认后提交）；调用方/上游声明的 tool call
  与本路径完全无关（它们只作为数据转发，见协议层）。
- 每次执行完整审计（成功与拒绝都记录）。
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.constants import (
    TOOL_MAX_ARGUMENTS,
    TOOL_MAX_COMMAND_LENGTH,
    TOOL_MAX_NAME_LENGTH,
    TOOL_MAX_TIMEOUT_SECONDS,
    TOOL_MIN_TIMEOUT_SECONDS,
)
from ...core.db import begin_immediate_if_sqlite
from ...domain.enums import (
    AuditAction,
    AuditResult,
    ToolExecutionState,
    UserRole,
)
from ...domain.errors import DomainError, DomainErrorCode
from ...repositories.models import ToolExecution, ToolWhitelist, User
from ...repositories.system import AuditRepository
from .sandbox import render_command, run_sandboxed, validate_command_template


def _validate_arguments_schema(schema: dict[str, Any]) -> list[str]:
    """校验参数 schema：仅支持 string 类型属性（命令行参数形态）。

    复杂 schema（number/array 等）M12 不开放——命令行渲染需要确定性的
    字符串值，避免类型歧义。
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "arguments_schema 必须是 {type: object, properties: {...}}",
            status_code=400,
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "arguments_schema.properties 必须是对象（无参工具用空对象）",
            status_code=400,
        )
    if len(properties) > TOOL_MAX_ARGUMENTS:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"参数数量不能超过 {TOOL_MAX_ARGUMENTS}",
            status_code=400,
        )
    names: list[str] = []
    for name, prop in properties.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"参数名非法: {name}",
                status_code=400,
            )
        if not isinstance(prop, dict) or prop.get("type") != "string":
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"参数 {name} 必须声明为 string 类型",
                status_code=400,
            )
        names.append(name)
    return names


def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, str]:
    """运行时参数校验：键集合与 schema 一致（required 全给、不给未声明键），值全字符串。"""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or properties.keys())
    given = set(arguments.keys())
    missing = required - given
    unknown = given - set(properties.keys())
    if missing:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"缺少必填参数: {', '.join(sorted(missing))}",
            status_code=400,
        )
    if unknown:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"包含未声明的参数: {', '.join(sorted(unknown))}",
            status_code=400,
        )
    result: dict[str, str] = {}
    for key in given:
        value = arguments[key]
        if not isinstance(value, str) or not value:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"参数 {key} 必须是非空字符串",
                status_code=400,
            )
        if len(value) > 2000:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"参数 {key} 过长（上限 2000 字符）",
                status_code=400,
            )
        result[key] = value
    return result


class ToolService:
    def __init__(self) -> None:
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 白名单 CRUD（管理员）
    # ------------------------------------------------------------------

    def list_whitelist(
        self, session: Session, *, include_disabled: bool = True
    ) -> list[ToolWhitelist]:
        filters = []
        if not include_disabled:
            filters.append(ToolWhitelist.is_enabled.is_(True))
        return list(
            session.scalars(
                select(ToolWhitelist).where(*filters).order_by(ToolWhitelist.name.asc())
            )
        )

    def list_enabled_for_user(self, session: Session) -> list[ToolWhitelist]:
        """用户可见：仅白名单内已启用工具（只读目录）。"""
        return self.list_whitelist(session, include_disabled=False)

    def create(
        self,
        session: Session,
        *,
        actor: User,
        name: str,
        description: str | None,
        command_template: str,
        arguments_schema: dict[str, Any],
        timeout_seconds: int,
    ) -> ToolWhitelist:
        if actor.role is not UserRole.ADMIN:
            raise DomainError(DomainErrorCode.FORBIDDEN, "需要管理员权限", status_code=403)
        begin_immediate_if_sqlite(session)
        cleaned_name = (name or "").strip()
        if not cleaned_name or len(cleaned_name) > TOOL_MAX_NAME_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具名不能为空且不超过 {TOOL_MAX_NAME_LENGTH} 字符",
                status_code=400,
            )
        if not command_template or len(command_template) > TOOL_MAX_COMMAND_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"命令模板不能为空且不超过 {TOOL_MAX_COMMAND_LENGTH} 字符",
                status_code=400,
            )
        if not (TOOL_MIN_TIMEOUT_SECONDS <= timeout_seconds <= TOOL_MAX_TIMEOUT_SECONDS):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"超时必须在 {TOOL_MIN_TIMEOUT_SECONDS}-{TOOL_MAX_TIMEOUT_SECONDS} 秒之间",
                status_code=400,
            )
        argument_names = _validate_arguments_schema(arguments_schema)
        validate_command_template(command_template, argument_names)
        existing = session.execute(
            select(ToolWhitelist).where(ToolWhitelist.name == cleaned_name)
        ).scalar_one_or_none()
        if existing is not None:
            raise DomainError(DomainErrorCode.CONFLICT, "同名工具已存在", status_code=409)
        row = ToolWhitelist(
            name=cleaned_name,
            description=(description or "").strip() or None,
            command_template=command_template,
            arguments_schema_json=json.dumps(arguments_schema, ensure_ascii=False),
            timeout_seconds=timeout_seconds,
            is_enabled=True,
        )
        session.add(row)
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.TOOL_WHITELIST_CREATED,
            resource_type="tool_whitelist",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            metadata={
                "fields": ["name", "command_template", "arguments_schema", "timeout_seconds"]
            },
        )
        return row

    def update(
        self,
        session: Session,
        *,
        actor: User,
        row: ToolWhitelist,
        fields: dict[str, Any],
    ) -> ToolWhitelist:
        if actor.role is not UserRole.ADMIN:
            raise DomainError(DomainErrorCode.FORBIDDEN, "需要管理员权限", status_code=403)
        begin_immediate_if_sqlite(session)
        changed: list[str] = []
        if "description" in fields and fields["description"] is not None:
            new_desc = (fields["description"] or "").strip() or None
            if new_desc != row.description:
                row.description = new_desc
                changed.append("description")
        if "command_template" in fields and fields["command_template"] is not None:
            template = fields["command_template"]
            if len(template) > TOOL_MAX_COMMAND_LENGTH:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "命令模板过长", status_code=400
                )
            schema = json.loads(row.arguments_schema_json)
            validate_command_template(template, _validate_arguments_schema(schema))
            if template != row.command_template:
                row.command_template = template
                changed.append("command_template")
        if "arguments_schema" in fields and fields["arguments_schema"] is not None:
            schema = fields["arguments_schema"]
            names = _validate_arguments_schema(schema)
            validate_command_template(row.command_template, names)
            row.arguments_schema_json = json.dumps(schema, ensure_ascii=False)
            changed.append("arguments_schema")
        if "timeout_seconds" in fields and fields["timeout_seconds"] is not None:
            value = int(fields["timeout_seconds"])
            if not (TOOL_MIN_TIMEOUT_SECONDS <= value <= TOOL_MAX_TIMEOUT_SECONDS):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "超时范围非法", status_code=400
                )
            if value != row.timeout_seconds:
                row.timeout_seconds = value
                changed.append("timeout_seconds")
        if "is_enabled" in fields and fields["is_enabled"] is not None:
            new_enabled = bool(fields["is_enabled"])
            if new_enabled != row.is_enabled:
                row.is_enabled = new_enabled
                changed.append("is_enabled")
        if changed:
            session.flush()
            self.audit.add(
                session,
                action=AuditAction.TOOL_WHITELIST_UPDATED,
                resource_type="tool_whitelist",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                metadata={"fields": changed},
            )
        return row

    def delete(self, session: Session, *, actor: User, row: ToolWhitelist) -> None:
        if actor.role is not UserRole.ADMIN:
            raise DomainError(DomainErrorCode.FORBIDDEN, "需要管理员权限", status_code=403)
        begin_immediate_if_sqlite(session)
        session.delete(row)
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.TOOL_WHITELIST_DELETED,
            resource_type="tool_whitelist",
            resource_id=str(row.id),
            actor_user_id=actor.id,
        )

    # ------------------------------------------------------------------
    # 用户执行（显式确认 + 审计）
    # ------------------------------------------------------------------

    def execute(
        self,
        session: Session,
        *,
        user: User,
        tool_id: int,
        arguments: dict[str, Any],
        confirmed: bool,
    ) -> ToolExecution:
        """执行白名单工具。confirmed=False 直接拒绝并审计（显式确认语义）。"""
        begin_immediate_if_sqlite(session)
        if not confirmed:
            # 显式确认缺失：拒绝并审计拒绝事件（拒绝审计需落库后抛出）。
            self.audit.add(
                session,
                action=AuditAction.TOOL_EXECUTION_DENIED,
                resource_type="tool_whitelist",
                resource_id=str(tool_id),
                actor_user_id=user.id,
                result=AuditResult.DENIED,
                metadata={"reason": "not_confirmed"},
            )
            session.commit()
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "工具执行需要显式确认",
                status_code=400,
            )
        tool = session.get(ToolWhitelist, tool_id)
        if tool is None:
            self.audit.add(
                session,
                action=AuditAction.TOOL_EXECUTION_DENIED,
                resource_type="tool_whitelist",
                resource_id=str(tool_id),
                actor_user_id=user.id,
                result=AuditResult.DENIED,
                metadata={"reason": "not_found"},
            )
            session.commit()
            raise DomainError(DomainErrorCode.NOT_FOUND, "工具不存在", status_code=404)
        if not tool.is_enabled:
            self.audit.add(
                session,
                action=AuditAction.TOOL_EXECUTION_DENIED,
                resource_type="tool_whitelist",
                resource_id=str(tool_id),
                actor_user_id=user.id,
                result=AuditResult.DENIED,
                metadata={"reason": "disabled"},
            )
            session.commit()
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "工具已停用", status_code=400)
        schema = json.loads(tool.arguments_schema_json)
        try:
            clean_args = _validate_arguments(schema, arguments)
        except DomainError:
            self.audit.add(
                session,
                action=AuditAction.TOOL_EXECUTION_DENIED,
                resource_type="tool_whitelist",
                resource_id=str(tool_id),
                actor_user_id=user.id,
                result=AuditResult.DENIED,
                metadata={"reason": "invalid_arguments", "fields": ["arguments"]},
            )
            session.commit()
            raise

        argv = render_command(tool.command_template, clean_args)
        row = ToolExecution(
            tool_id=tool.id,
            user_id=user.id,
            arguments_json=json.dumps(clean_args, ensure_ascii=False),
            state=ToolExecutionState.RUNNING,
        )
        session.add(row)
        session.flush()

        result = run_sandboxed(argv, timeout_seconds=tool.timeout_seconds)
        state_map = {
            "succeeded": ToolExecutionState.SUCCEEDED,
            "failed": ToolExecutionState.FAILED,
            "timed_out": ToolExecutionState.TIMED_OUT,
            "limit_exceeded": ToolExecutionState.LIMIT_EXCEEDED,
        }
        row.state = state_map[result.state]
        row.exit_code = result.exit_code
        row.stdout_text = result.stdout or None
        row.stderr_text = result.stderr or None
        row.error_code = result.error_code
        row.duration_ms = result.duration_ms
        session.flush()
        audit_result = AuditResult.SUCCESS if result.state == "succeeded" else AuditResult.FAILED
        self.audit.add(
            session,
            action=AuditAction.TOOL_EXECUTED,
            resource_type="tool_whitelist",
            resource_id=str(tool.id),
            actor_user_id=user.id,
            result=audit_result,
            metadata={
                "fields": ["arguments", "exit_code", "state", "duration_ms"],
                "execution_id": str(row.id),
            },
        )
        return row

    def list_executions(
        self, session: Session, *, user: User, page: int, page_size: int
    ) -> tuple[list[ToolExecution], int]:
        """用户查看自己的执行历史；管理员看全部。"""
        filters: list[Any] = []
        if user.role is not UserRole.ADMIN:
            filters.append(ToolExecution.user_id == user.id)
        total = session.scalar(select(func.count()).select_from(ToolExecution).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(ToolExecution)
                .where(*filters)
                .order_by(ToolExecution.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def get_execution(self, session: Session, *, user: User, execution_id: int) -> ToolExecution:
        row = session.get(ToolExecution, execution_id)
        if row is None or (user.role is not UserRole.ADMIN and row.user_id != user.id):
            raise DomainError(DomainErrorCode.NOT_FOUND, "执行记录不存在", status_code=404)
        return row


def quote_hint(value: str) -> str:
    """文档性导出：参数值经 shlex.quote 注入防护。"""
    return shlex.quote(value)

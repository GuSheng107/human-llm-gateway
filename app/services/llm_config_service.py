"""LLM 配置用例：CRUD、Secret 加密、连通性测试与引用安全删除（M7-A）。

LLM Secret 和自定义 Header 整体按 Secret 处理：服务端加密落库，
任何响应（列表 / 详情 / 连通性测试）只返回"已设置 / 未设置"标记，
绝不回显明文。被 API Key 或活动任务引用的配置不允许删除，返回 409。
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.constants import (
    LLM_BASE_URL_MAX_LENGTH,
    LLM_HEADER_NAME_MAX_LENGTH,
    LLM_HEADER_VALUE_MAX_LENGTH,
    LLM_MAX_HEADERS,
    LLM_MODEL_MAX_LENGTH,
    LLM_NAME_MAX_LENGTH,
    LLM_TIMEOUT_DEFAULT_SECONDS,
    LLM_TIMEOUT_MAX_SECONDS,
    LLM_TIMEOUT_MIN_SECONDS,
)
from ..core.db import begin_immediate_if_sqlite
from ..core.security import decrypt_secret, encrypt_secret
from ..domain.enums import (
    AuditAction,
    LLMProtocol,
    ThinkingLevel,
    ThinkingMode,
    UserRole,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.llm_configs import LlmConfigRepository
from ..repositories.models import LlmConfig, User
from ..repositories.system import AuditRepository

_LLM_SECRET_PURPOSE = "llm-config"


def _normalize_base_url(raw: str, protocol: LLMProtocol | None = None) -> str:
    """去尾斜杠与空白；要求 http(s) scheme + host。

    Anthropic 协议自动补齐 /v1 前缀（官方 SDK base_url 即 https://host，
    endpoint 为 /v1/messages）：填 https://host 或 https://host/v1 均可，
    统一归一为后者，消除"OpenAI 要带 /v1、Anthropic 不带"的不对称。
    OpenAI 兼容协议保持用户原样（生态中网关路径不一，不做猜测）。
    """
    value = (raw or "").strip()
    if not value:
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "base_url 不能为空", status_code=400)
    if len(value) > LLM_BASE_URL_MAX_LENGTH:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"base_url 长度不能超过 {LLM_BASE_URL_MAX_LENGTH}",
            status_code=400,
        )
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "base_url 必须使用 http 或 https scheme",
            status_code=400,
        )
    if not parsed.netloc:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "base_url 必须包含主机", status_code=400
        )
    cleaned = value.rstrip("/")
    if protocol is LLMProtocol.ANTHROPIC_MESSAGES:
        # path 为空或已是 /v1（或 /v1/ 结尾已 rstrip）时补齐/保持；
        # 其他自定义 path（代理场景）原样保留。
        path = parsed.path.rstrip("/")
        if path == "":
            cleaned = f"{cleaned}/v1"
    # SSRF 分档校验：云元数据无条件拒；私有段受配置开关控制。
    # 域名走 getaddrinfo 全量解析（含 rebinding fail-closed）。
    from ..core.ssrf import SsrfViolation, validate_base_url

    try:
        validate_base_url(cleaned)
    except SsrfViolation as exc:
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, str(exc), status_code=400) from exc
    return cleaned


def _normalize_headers(raw: dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    if len(raw) > LLM_MAX_HEADERS:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"自定义 Header 数量不能超过 {LLM_MAX_HEADERS}",
            status_code=400,
        )
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "自定义 Header 必须为字符串键值对",
                status_code=400,
            )
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if not cleaned_key:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "自定义 Header 名不能为空",
                status_code=400,
            )
        if len(cleaned_key) > LLM_HEADER_NAME_MAX_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"自定义 Header 名长度不能超过 {LLM_HEADER_NAME_MAX_LENGTH}",
                status_code=400,
            )
        if len(cleaned_value) > LLM_HEADER_VALUE_MAX_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"自定义 Header 值长度不能超过 {LLM_HEADER_VALUE_MAX_LENGTH}",
                status_code=400,
            )
        if cleaned_key.lower() in {"authorization", "x-api-key"}:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"自定义 Header {cleaned_key} 不允许（API Key 通过 api_key 字段管理）",
                status_code=400,
            )
        # 大小写不敏感去重：Foo 与 foo 在 HTTP 语义中同名，显式拒绝
        # 而非静默覆盖。
        if cleaned_key.lower() in {k.lower() for k in normalized}:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"自定义 Header {cleaned_key} 与已有 Header 大小写冲突",
                status_code=400,
            )
        normalized[cleaned_key] = cleaned_value
    return normalized


class LlmConfigService:
    def __init__(self) -> None:
        self.repo = LlmConfigRepository()
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_for_user(
        self,
        session: Session,
        *,
        user: User,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[LlmConfig], int]:
        owner_filter = None if user.role is UserRole.ADMIN else user.id
        return self.repo.list_page(
            session, page=page, page_size=page_size, owner_user_id=owner_filter, search=search
        )

    def get_owned(self, session: Session, config_id: int, user: User) -> LlmConfig:
        row = self.repo.get(session, config_id)
        if row is None or (user.role is not UserRole.ADMIN and row.owner_user_id != user.id):
            raise DomainError(DomainErrorCode.NOT_FOUND, "LLM 配置不存在", status_code=404)
        return row

    @staticmethod
    def get_secret_pair(session: Session, row: LlmConfig) -> tuple[str, dict[str, str]]:
        """解密配置的 Secret 与自定义 Header（服务层内部共用）。

        不做归属校验（调用方已完成）；Secret 仅在内存使用，不进入响应。
        """
        try:
            secret = decrypt_secret(
                row.secret_ciphertext, get_settings().app_secret, _LLM_SECRET_PURPOSE
            )
        except Exception as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "LLM Secret 解密失败，请重新保存配置",
                status_code=409,
            ) from exc
        headers: dict[str, str] = {}
        if row.headers_ciphertext:
            try:
                decoded = decrypt_secret(
                    row.headers_ciphertext, get_settings().app_secret, _LLM_SECRET_PURPOSE
                )
                headers = json.loads(decoded)
            except (ValueError, json.JSONDecodeError) as exc:
                raise DomainError(
                    DomainErrorCode.CONFLICT,
                    "LLM 自定义 Header 解密失败，请重新保存配置",
                    status_code=409,
                ) from exc
        return secret, headers

    def get_owned_with_secret(
        self, session: Session, config_id: int, user: User
    ) -> tuple[LlmConfig, str, dict[str, str]]:
        """取配置并解密 Secret / Headers。仅供服务层内部调用（如连通性测试）。

        Secret 与 Header 不进入响应，由调用方使用后丢弃。
        """
        row = self.get_owned(session, config_id, user)
        try:
            secret = decrypt_secret(
                row.secret_ciphertext, get_settings().app_secret, _LLM_SECRET_PURPOSE
            )
        except Exception as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "LLM Secret 解密失败，请重新保存配置",
                status_code=409,
            ) from exc
        headers: dict[str, str] = {}
        if row.headers_ciphertext:
            try:
                decoded = decrypt_secret(
                    row.headers_ciphertext,
                    get_settings().app_secret,
                    _LLM_SECRET_PURPOSE,
                )
                headers = json.loads(decoded)
            except Exception as exc:
                raise DomainError(
                    DomainErrorCode.CONFLICT,
                    "LLM 自定义 Header 解密失败，请重新保存配置",
                    status_code=409,
                ) from exc
        return row, secret, headers

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    def create(
        self,
        session: Session,
        *,
        owner: User,
        name: str,
        protocol: LLMProtocol,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
        default_temperature: Decimal | None = None,
        default_top_p: Decimal | None = None,
        default_top_k: int | None = None,
        max_output_tokens: int | None = None,
        context_window_input: int | None = None,
        context_window_output: int | None = None,
        max_tool_call_rounds: int = 16,
        supports_image_input: bool = False,
        thinking_mode: ThinkingMode = ThinkingMode.MODEL_DEFAULT,
        thinking_level: ThinkingLevel | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LlmConfig:
        begin_immediate_if_sqlite(session)
        self._validate_name(name)
        normalized_url = _normalize_base_url(base_url, protocol)
        self._validate_model(model)
        self._validate_timeout(timeout_seconds)
        self._validate_sampling(
            temperature=default_temperature,
            top_p=default_top_p,
            top_k=default_top_k,
            max_output_tokens=max_output_tokens,
            context_window_input=context_window_input,
            context_window_output=context_window_output,
            max_tool_call_rounds=max_tool_call_rounds,
        )
        self._validate_thinking(protocol, thinking_mode, thinking_level)
        normalized_extra = extra_body or {}
        if not isinstance(normalized_extra, dict):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "extra_body 必须是 JSON 对象", status_code=400
            )
        normalized_headers = _normalize_headers(headers)
        if not api_key:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "api_key 不能为空",
                status_code=400,
            )
        if self.repo.get_by_name(session, owner_user_id=owner.id, name=name.strip()):
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "同名 LLM 配置已存在",
                status_code=409,
            )
        row = LlmConfig(
            owner_user_id=owner.id,
            name=name.strip(),
            protocol=protocol,
            base_url=normalized_url,
            real_model=model.strip(),
            encryption_key_version=1,
            timeout_seconds=timeout_seconds,
            is_enabled=enabled,
            secret_ciphertext=encrypt_secret(
                api_key, get_settings().app_secret, _LLM_SECRET_PURPOSE
            ),
            headers_ciphertext=(
                encrypt_secret(
                    json.dumps(normalized_headers, ensure_ascii=False),
                    get_settings().app_secret,
                    _LLM_SECRET_PURPOSE,
                )
                if normalized_headers
                else None
            ),
            default_temperature=default_temperature,
            default_top_p=default_top_p,
            default_top_k=default_top_k,
            max_output_tokens=max_output_tokens,
            context_window_input=context_window_input,
            context_window_output=context_window_output,
            max_tool_call_rounds=max_tool_call_rounds,
            supports_image_input=supports_image_input,
            thinking_mode=thinking_mode,
            thinking_level=thinking_level,
            extra_body=normalized_extra,
        )
        self.repo.add(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT, "同名 LLM 配置已存在", status_code=409
            ) from exc
        self.audit.add(
            session,
            action=AuditAction.LLM_CONFIG_CREATED,
            resource_type="llm_config",
            resource_id=str(row.id),
            actor_user_id=owner.id,
            owner_user_id=owner.id,
            metadata={
                "fields": [
                    "name",
                    "protocol",
                    "base_url_host",
                    "real_model",
                    "headers_set",
                    "timeout_seconds",
                    "is_enabled",
                ]
            },
        )
        return row

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update(
        self,
        session: Session,
        *,
        row: LlmConfig,
        actor: User,
        fields: dict[str, Any],
    ) -> LlmConfig:
        begin_immediate_if_sqlite(session)
        owner_id = row.owner_user_id
        changed: list[str] = []

        if "name" in fields and fields["name"] is not None:
            new_name = (fields["name"] or "").strip()
            self._validate_name(new_name)
            if new_name != row.name:
                if self.repo.get_by_name(session, owner_user_id=owner_id, name=new_name):
                    raise DomainError(
                        DomainErrorCode.CONFLICT,
                        "同名 LLM 配置已存在",
                        status_code=409,
                    )
                row.name = new_name
                changed.append("name")

        if "protocol" in fields and fields["protocol"] is not None:
            try:
                proto = LLMProtocol(fields["protocol"])
            except ValueError as exc:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"不支持的 LLM 协议: {fields['protocol']}",
                    status_code=400,
                ) from exc
            if proto is not row.protocol:
                row.protocol = proto
                changed.append("protocol")

        if "base_url" in fields and fields["base_url"] is not None:
            # 协议字段（若同请求更新）已先行写入 row，归一化按目标协议执行；
            # 协议切换后旧 base_url 形态会被重新归一（如 anthropic 补 /v1）。
            normalized = _normalize_base_url(fields["base_url"], row.protocol)
            if normalized != row.base_url:
                row.base_url = normalized
                changed.append("base_url")
        elif "protocol" in fields and changed and "protocol" in changed:
            # 仅切协议未提交 base_url：对既有 base_url 按新协议重新归一。
            renormalized = _normalize_base_url(row.base_url, row.protocol)
            if renormalized != row.base_url:
                row.base_url = renormalized
                changed.append("base_url")

        if "model" in fields and fields["model"] is not None:
            new_model = (fields["model"] or "").strip()
            self._validate_model(new_model)
            if new_model != row.real_model:
                row.real_model = new_model
                changed.append("model")

        if "timeout_seconds" in fields and fields["timeout_seconds"] is not None:
            self._validate_timeout(int(fields["timeout_seconds"]))
            if int(fields["timeout_seconds"]) != row.timeout_seconds:
                row.timeout_seconds = int(fields["timeout_seconds"])
                changed.append("timeout_seconds")

        if "enabled" in fields and fields["enabled"] is not None:
            new_enabled = bool(fields["enabled"])
            if new_enabled != row.is_enabled:
                row.is_enabled = new_enabled
                changed.append("is_enabled")

        if "api_key" in fields:
            new_key = fields["api_key"]
            # PATCH 语义：None / 空串 / 纯空白一律视为"保留旧值"（与前端
            # "留空表示保留旧值"提示一致）；仅显式提交非空字符串才轮换密钥。
            if isinstance(new_key, str) and new_key.strip():
                row.secret_ciphertext = encrypt_secret(
                    new_key.strip(), get_settings().app_secret, _LLM_SECRET_PURPOSE
                )
                changed.append("api_key")

        if "headers" in fields:
            new_headers = _normalize_headers(fields["headers"] or {})
            if new_headers:
                row.headers_ciphertext = encrypt_secret(
                    json.dumps(new_headers, ensure_ascii=False),
                    get_settings().app_secret,
                    _LLM_SECRET_PURPOSE,
                )
            else:
                row.headers_ciphertext = None
            changed.append("headers")

        # ---- 采样参数（None 表示清空，缺省 key 表示保留）----
        for field_name in (
            "default_temperature",
            "default_top_p",
            "default_top_k",
            "max_output_tokens",
            "context_window_input",
            "context_window_output",
            "max_tool_call_rounds",
        ):
            if field_name not in fields:
                continue
            value = fields[field_name]
            if field_name.startswith("default_") and field_name != "default_top_k":
                value = self._coerce_decimal(value, field_name)
            elif value is not None and value != "":
                value = int(value)
                if value < 1:
                    raise DomainError(
                        DomainErrorCode.VALIDATION_FAILED,
                        f"{field_name} 必须为正整数",
                        status_code=400,
                    )
            else:
                value = None
            if getattr(row, field_name) != value:
                setattr(row, field_name, value)
                changed.append(field_name)

        if "supports_image_input" in fields and fields["supports_image_input"] is not None:
            new_value = bool(fields["supports_image_input"])
            if new_value != row.supports_image_input:
                row.supports_image_input = new_value
                changed.append("supports_image_input")

        if "thinking_mode" in fields and fields["thinking_mode"] is not None:
            try:
                new_mode = ThinkingMode(fields["thinking_mode"])
            except ValueError as exc:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"不支持的思考模式: {fields['thinking_mode']}",
                    status_code=400,
                ) from exc
            if new_mode != row.thinking_mode:
                row.thinking_mode = new_mode
                changed.append("thinking_mode")

        if "thinking_level" in fields:
            try:
                new_level = (
                    ThinkingLevel(fields["thinking_level"]) if fields["thinking_level"] else None
                )
            except ValueError as exc:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"不支持的思考等级: {fields['thinking_level']}",
                    status_code=400,
                ) from exc
            if new_level != row.thinking_level:
                row.thinking_level = new_level
                changed.append("thinking_level")

        if "extra_body" in fields and fields["extra_body"] is not None:
            new_extra = fields["extra_body"]
            if not isinstance(new_extra, dict):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "extra_body 必须是 JSON 对象",
                    status_code=400,
                )
            if new_extra != row.extra_body:
                row.extra_body = new_extra
                changed.append("extra_body")

        # 采样区间 + 思考等级联动校验（协议可能同请求切换，以行终态为准）。
        self._validate_sampling(
            temperature=row.default_temperature,
            top_p=row.default_top_p,
            top_k=row.default_top_k,
            max_output_tokens=row.max_output_tokens,
            context_window_input=row.context_window_input,
            context_window_output=row.context_window_output,
            max_tool_call_rounds=row.max_tool_call_rounds,
        )
        self._validate_thinking(row.protocol, row.thinking_mode, row.thinking_level)

        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT, "同名 LLM 配置已存在", status_code=409
            ) from exc
        if changed:
            self.audit.add(
                session,
                action=AuditAction.LLM_CONFIG_UPDATED,
                resource_type="llm_config",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                owner_user_id=owner_id,
                metadata={"fields": changed},
            )
        return row

    # ------------------------------------------------------------------
    # 删除（被引用时拒绝，软删除并清空 Secret）
    # ------------------------------------------------------------------

    def delete(self, session: Session, *, row: LlmConfig, actor: User) -> None:
        begin_immediate_if_sqlite(session)
        key_refs = self.repo.count_api_key_references(session, config_id=row.id)
        task_refs = self.repo.count_active_task_references(session, config_id=row.id)
        if key_refs or task_refs:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                (
                    f"LLM 配置仍被 {key_refs} 个 API Key"
                    + (f" 与 {task_refs} 个活动任务" if task_refs else "")
                    + " 引用，无法删除"
                ),
                status_code=409,
            )
        self.repo.soft_delete(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.LLM_CONFIG_DELETED,
            resource_type="llm_config",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["deleted_at", "is_enabled", "secret", "headers"]},
        )

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not name.strip():
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "名称不能为空", status_code=400)
        if len(name.strip()) > LLM_NAME_MAX_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"名称长度不能超过 {LLM_NAME_MAX_LENGTH}",
                status_code=400,
            )

    @staticmethod
    def _validate_model(model: str) -> None:
        if not model or not model.strip():
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "model 不能为空", status_code=400)
        if len(model.strip()) > LLM_MODEL_MAX_LENGTH:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"model 长度不能超过 {LLM_MODEL_MAX_LENGTH}",
                status_code=400,
            )

    @staticmethod
    def _validate_timeout(seconds: int) -> None:
        if seconds < LLM_TIMEOUT_MIN_SECONDS or seconds > LLM_TIMEOUT_MAX_SECONDS:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"timeout_seconds 必须在 {LLM_TIMEOUT_MIN_SECONDS} 到 {LLM_TIMEOUT_MAX_SECONDS} 之间",
                status_code=400,
            )

    @staticmethod
    def _validate_sampling(
        *,
        temperature: Decimal | None,
        top_p: Decimal | None,
        top_k: int | None,
        max_output_tokens: int | None,
        context_window_input: int | None,
        context_window_output: int | None,
        max_tool_call_rounds: int | None,
    ) -> None:
        def bad(field: str, message: str) -> DomainError:
            return DomainError(
                DomainErrorCode.VALIDATION_FAILED, f"{field}: {message}", status_code=400
            )

        if temperature is not None and not (0 <= temperature <= 2):
            raise bad("temperature", "必须在 0.00-2.00")
        if top_p is not None and not (0 <= top_p <= 1):
            raise bad("top_p", "必须在 0.00-1.00")
        if top_k is not None and not (1 <= top_k <= 100):
            raise bad("top_k", "必须在 1-100")
        for field_name, value in (
            ("max_output_tokens", max_output_tokens),
            ("context_window_input", context_window_input),
            ("context_window_output", context_window_output),
            ("max_tool_call_rounds", max_tool_call_rounds),
        ):
            if value is not None and value < 1:
                raise bad(field_name, "必须为正整数")

    @staticmethod
    def _validate_thinking(
        protocol: LLMProtocol, mode: ThinkingMode, level: ThinkingLevel | None
    ) -> None:
        if level is not None and protocol is not LLMProtocol.OPENAI_RESPONSES:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "思考等级仅支持 OpenAI Responses 协议",
                status_code=400,
            )
        if (
            protocol is LLMProtocol.OPENAI_RESPONSES
            and mode is ThinkingMode.ENABLED
            and level is None
        ):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "开启思考模式时必须指定思考等级",
                status_code=400,
            )

    @staticmethod
    def _coerce_decimal(value: Any, field: str) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, f"{field} 必须是数字", status_code=400
            ) from exc


def default_timeout_seconds() -> int:
    return LLM_TIMEOUT_DEFAULT_SECONDS

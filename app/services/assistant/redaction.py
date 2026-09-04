"""Web 小助手上下文脱敏（docs/API_CONTRACT.md §10、AGENTS.md §5）。

双层防线（借鉴 allowlist 哲学，结构性排除优先于模式匹配）：

1. 封闭 schema（主防线）：上下文必须是 StrictModel 声明的结构，未知字段
   直接 400 拒收（不是擦洗）；feature 与 resource 键都是白名单枚举——
   前端 API 结构上采不到明文 Secret（列表/详情只返回 key_prefix/api_key_set），
   schema 把"走私通道"封死。
2. 自由文本擦洗（最后防线）：resource 值、unsaved_edit 的 reasoning /
   final_text、tool_calls.arguments 的值过已知凭据模式正则——用户可能
   把 API Key/密码粘贴进编辑器再问小助手，这是唯一的自由文本入口。

被擦洗的项替换为 [REDACTED]（保留结构）；发生次数记结构化日志，
不记原值。
"""

from __future__ import annotations

import re

from ...core.logging import log_event
from ...domain.errors import DomainError, DomainErrorCode

# 已知凭据形态：本系统 API Key、OpenAI/Anthropic 兼容 Key、Bearer 头、
# Secret envelope、PEM 私钥头。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}"),  # 本系统及兼容服务 API Key
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),  # Authorization 头
    re.compile(r"hlg1\.[0-9]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # Secret envelope
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM 私钥
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}"),  # 键值式凭据
)

# resource 键白名单：按 feature 声明允许的键（结构层第二道白名单）。
_FEATURE_RESOURCE_KEYS: dict[str, frozenset[str]] = {
    "task_detail": frozenset(
        {"task_id", "public_id", "state", "model", "protocol", "strategy", "delivery"}
    ),
    "task_list": frozenset({"state_filter", "search"}),
    "replies": frozenset({"task_id", "state_filter"}),
    "api_keys": frozenset({"api_key_id", "name", "strategy", "delivery"}),
    "llm_configs": frozenset({"llm_config_id", "name", "protocol", "real_model"}),
    "connections": frozenset({"connection_id", "name", "platform", "state"}),
    "models": frozenset({"model_id", "scope", "group_name"}),
    "invitations": frozenset({"invitation_id", "state"}),
    "users": frozenset({"user_id", "username", "is_active"}),
    "console": frozenset({"section"}),
    "account": frozenset({"section"}),
    "tools": frozenset({"tool_id", "name"}),
    "logs": frozenset({"trace_id", "search"}),
    "adminConnections": frozenset({"connection_id", "platform"}),
}

_REDACTED = "[REDACTED]"
# 上下文 JSON 总字节上限（防超大快照占库/占上游 token）。
_MAX_CONTEXT_BYTES = 64 * 1024


def redact_text(text: str) -> tuple[str, int]:
    """擦洗自由文本中的已知凭据形态；返回 (结果, 命中次数)。"""
    hits = 0
    result = text
    for pattern in _SECRET_PATTERNS:
        result, n = pattern.subn(_REDACTED, result)
        hits += n
    return result, hits


def _redact_value(value: str) -> tuple[str, int]:
    return redact_text(value)


def validate_feature(feature: str) -> frozenset[str]:
    """feature 必须已注册；返回其 resource 键白名单。"""
    keys = _FEATURE_RESOURCE_KEYS.get(feature)
    if keys is None:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"未知的页面上下文 feature: {feature}",
            status_code=400,
        )
    return keys


def build_page_context(
    *,
    route: str,
    feature: str,
    resource: dict[str, str],
    unsaved_edit: dict | None,
    context_version: int,
) -> tuple[dict, int]:
    """校验 + 脱敏页面上下文；返回 (干净上下文 dict, 擦洗命中总数)。

    任何未知 feature / resource 键 / 结构违规 -> 400 拒收（allowlist）。
    """
    allowed_keys = validate_feature(feature)
    unknown = set(resource) - allowed_keys
    if unknown:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"resource 包含未声明的键: {', '.join(sorted(unknown))}",
            status_code=400,
        )

    total_hits = 0
    clean_resource: dict[str, str] = {}
    for key, value in resource.items():
        if not isinstance(value, str):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"resource.{key} 必须是字符串",
                status_code=400,
            )
        if len(value) > 2048:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"resource.{key} 过长（上限 2048 字符）",
                status_code=400,
            )
        cleaned, hits = _redact_value(value)
        clean_resource[key] = cleaned
        total_hits += hits

    clean_edit: dict | None = None
    if unsaved_edit is not None:
        clean_edit, hits = _redact_unsaved_edit(unsaved_edit)
        total_hits += hits

    context: dict = {
        "route": route,
        "feature": feature,
        "resource": clean_resource,
        "context_version": context_version,
    }
    if clean_edit is not None:
        context["unsaved_edit"] = clean_edit

    import json

    if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "页面上下文过大",
            status_code=400,
        )
    return context, total_hits


def _redact_unsaved_edit(edit: dict) -> tuple[dict, int]:
    """脱敏未提交编辑内容：reasoning / final_text / tool_calls.arguments 值。"""
    hits_total = 0
    clean: dict = {}
    reasoning = edit.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, str) or len(reasoning) > 20000:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "unsaved_edit.reasoning 非法", status_code=400
            )
        cleaned, hits = redact_text(reasoning)
        clean["reasoning"] = cleaned
        hits_total += hits
    final_text = edit.get("final_text")
    if final_text is not None:
        if not isinstance(final_text, str) or len(final_text) > 40000:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "unsaved_edit.final_text 非法", status_code=400
            )
        cleaned, hits = redact_text(final_text)
        clean["final_text"] = cleaned
        hits_total += hits
    tool_calls = edit.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list) or len(tool_calls) > 20:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "unsaved_edit.tool_calls 非法", status_code=400
            )
        clean_calls: list[dict] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "unsaved_edit.tool_calls 条目非法",
                    status_code=400,
                )
            call_id = call.get("id", "")
            name = call.get("name", "")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "unsaved_edit.tool_calls 条目非法",
                    status_code=400,
                )
            arguments = call.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "unsaved_edit.tool_calls.arguments 必须是对象",
                    status_code=400,
                )
            clean_args: dict = {}
            for key, value in arguments.items():
                if isinstance(value, str):
                    cleaned, hits = redact_text(value)
                    clean_args[key] = cleaned
                    hits_total += hits
                else:
                    clean_args[key] = value
            clean_calls.append({"id": call_id, "name": name, "arguments": clean_args})
        clean["tool_calls"] = clean_calls
    return clean, hits_total


def log_redaction(message_id_hint: str, hits: int) -> None:
    """脱敏事件日志（只记次数，不记原值）。"""
    if hits > 0:
        log_event(
            "warning",
            "assistant.context_redacted",
            "页面上下文含疑似凭据，已擦洗",
            source=message_id_hint,
            redacted_count=hits,
        )

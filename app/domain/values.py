"""领域值对象：username 归一、密码策略、ReplyDraft 共享结构。"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

# username：登录标识仅允许 ASCII（Unicode 展示名由 display_name 承担）。
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")

_MIN_PASSWORD_CODEPOINTS = 15
_MAX_PASSWORD_CODEPOINTS = 128

# 明显弱密码 / 部署默认词（非穷尽，先挡最明显的）。
_BLOCKED_PASSWORDS = frozenset(
    {
        "password",
        "changeme",
        "change-me-now",
        "admin123",
        "123456789",
        "qwertyuiop",
        "iloveyou",
    }
)


def normalize_username(raw: str) -> str | None:
    """strip + ASCII 小写归一，匹配失败返回 None。"""
    normalized = raw.strip().lower()
    if not USERNAME_PATTERN.match(normalized):
        return None
    return normalized


def normalize_display_name(raw: str) -> str | None:
    normalized = raw.strip()
    if not normalized or len(normalized) > 100:
        return None
    return normalized


def normalize_password(raw: str) -> str:
    """密码 NFC 归一化后存储/哈希。"""
    return unicodedata.normalize("NFC", raw)


def password_problems(password: str, username: str = "") -> list[str]:
    """返回密码不满足策略的原因列表；空列表表示通过。"""
    problems: list[str] = []
    normalized = normalize_password(password)
    codepoints = len(normalized)
    if codepoints < _MIN_PASSWORD_CODEPOINTS:
        problems.append(f"密码至少需要 {_MIN_PASSWORD_CODEPOINTS} 个字符")
    if codepoints > _MAX_PASSWORD_CODEPOINTS:
        problems.append(f"密码最多 {_MAX_PASSWORD_CODEPOINTS} 个字符")
    lowered = normalized.lower()
    if lowered in _BLOCKED_PASSWORDS:
        problems.append("密码过于常见")
    if username and lowered == username.lower():
        problems.append("密码不能与用户名相同")
    return problems


class ReplyToolCall(BaseModel):
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class ReplyDraft(BaseModel):
    """IM DSL、Web 编辑器、LLM 草稿和三协议渲染器共享的唯一回复结构。

    协议专有 ID、SSE 序号和 finish reason 由渲染器生成，不反向写入此结构。
    """

    reasoning: str | None = None
    tool_calls: list[ReplyToolCall] = Field(default_factory=list)
    final_text: str | None = None

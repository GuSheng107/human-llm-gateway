"""领域值对象：username 归一、密码策略、ReplyDraft 共享结构。"""

from __future__ import annotations

import base64
import re
import string
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

# username：登录标识仅允许 ASCII（Unicode 展示名由 display_name 承担）。
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")

# 邮箱：常规 RFC 5322 简化形态，最长 255。
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# 头像：base64 解码后（原图）上限 256 KiB。
MAX_AVATAR_BYTES = 2 * 1024 * 1024

_MIN_PASSWORD_CODEPOINTS = 10
_MAX_PASSWORD_CODEPOINTS = 128

# 密码复杂度：必须同时包含英文字母、数字、符号（ASCII 可打印标点）三类。
# 空格与 Unicode 字符不参与三类判定，仅计入长度。
_ASCII_LETTERS = frozenset(string.ascii_letters)
_ASCII_DIGITS = frozenset(string.digits)
_ASCII_SYMBOLS = frozenset(string.punctuation)

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


def normalize_email(raw: str | None) -> str | None:
    """strip + 小写归一；空串视为 None；格式或超长返回 None。"""
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if not normalized:
        return None
    if len(normalized) > 255 or not EMAIL_PATTERN.match(normalized):
        return None
    return normalized


def normalize_avatar_base64(raw: str | None) -> str | None:
    """校验头像 base64（PNG/JPEG，原图 ≤ MAX_AVATAR_BYTES）。

    返回去除 data URL 前缀后的纯 base64 字符串；非法返回 None。
    传入 None 表示不修改头像（与空串清空头像区分）。
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("data:"):
        # data:image/png;base64,xxxx
        header, _, payload = value.partition(",")
        if "base64" not in header:
            return None
        value = payload
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None
    if len(decoded) > MAX_AVATAR_BYTES:
        return None
    if decoded[:8] == b"\x89PNG\r\n\x1a\n" or decoded[:3] == b"\xff\xd8\xff":
        return value
    return None


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
    if not any(ch in _ASCII_LETTERS for ch in normalized):
        problems.append("密码需包含至少一个英文字母")
    if not any(ch in _ASCII_DIGITS for ch in normalized):
        problems.append("密码需包含至少一个数字")
    if not any(ch in _ASCII_SYMBOLS for ch in normalized):
        problems.append("密码需包含至少一个符号")
    lowered = normalized.lower()
    if lowered in _BLOCKED_PASSWORDS:
        problems.append("密码过于常见")
    if username and lowered == username.lower():
        problems.append("密码不能与用户名相同")
    return problems


class ReplyToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReplyDraft(BaseModel):
    """IM DSL、Web 编辑器、LLM 草稿和三协议渲染器共享的唯一回复结构。

    协议专有 ID、SSE 序号和 finish reason 由渲染器生成，不反向写入此结构。
    """

    reasoning: str | None = None
    tool_calls: list[ReplyToolCall] = Field(default_factory=list)
    final_text: str | None = None

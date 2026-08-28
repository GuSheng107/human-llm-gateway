"""结构化日志敏感字段过滤测试。"""

from __future__ import annotations

from app.core.logging import sanitize_log_fields


def test_sensitive_fields_are_redacted_recursively() -> None:
    sanitized = sanitize_log_fields(
        {
            "request_id": "req_1",
            "api_key_id": 7,
            "password": "plain-password",
            "nested": {
                "access_token": "plain-token",
                "api_key": "hlg_plain",
                "api_key_prefix": "hlg_safe",
            },
        }
    )

    assert sanitized == {
        "request_id": "req_1",
        "api_key_id": 7,
        "password": "[REDACTED]",
        "nested": {
            "access_token": "[REDACTED]",
            "api_key": "[REDACTED]",
            "api_key_prefix": "hlg_safe",
        },
    }

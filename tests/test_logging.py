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
                "api_key": "sk-plain-secret-value",
                "api_key_prefix": "sk-safe1",
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
            "api_key_prefix": "sk-safe1",
        },
    }


def test_plaintext_credential_codes_are_redacted() -> None:
    sanitized = sanitize_log_fields(
        {
            "invitation_code": "ABCDEFGHJKLMNPQR",
            "binding_code": "ABCDEFGH",
            "code": "ABCDEFGHJKLMNPQR",
            "temporary_password": "plain-temp-password",
            "code_hash": "sha256$deadbeef",
        }
    )

    assert sanitized == {
        "invitation_code": "[REDACTED]",
        "binding_code": "[REDACTED]",
        "code": "[REDACTED]",
        "temporary_password": "[REDACTED]",
        "code_hash": "[REDACTED]",
    }

"""结构化日志敏感字段过滤测试。"""

from __future__ import annotations

from app.core.logging import get_log_queue, sanitize_log_fields


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


def test_info_record_from_notset_logger_is_persisted(client) -> None:
    """NOTSET 子 logger 的 INFO 记录必须进入落库队列。

    root logger 默认 WARNING：只放开 handler level 无效，记录会在源头
    isEnabledFor 被丢弃，故 install_persistence 必须同时放开 root level。
    """
    import logging

    root = logging.getLogger()
    queue = get_log_queue()
    previous_store = queue.direct_store
    captured: list[dict] = []
    queue.direct_store = captured
    try:
        assert root.level == logging.INFO
        probe = logging.getLogger("tests.probe.info")
        probe.setLevel(logging.NOTSET)
        probe.info("info probe message")
    finally:
        queue.direct_store = previous_store

    assert [entry["message"] for entry in captured] == ["info probe message"]
    assert captured[0]["level"] == "info"

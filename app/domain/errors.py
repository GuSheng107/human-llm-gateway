"""领域错误与稳定错误码。

领域错误码见 docs/ARCHITECTURE.md §7.3；协议层负责把它们映射为
OpenAI / Anthropic 兼容的 HTTP 状态和 JSON/SSE 错误，不把内部文本直接返回。
"""

from __future__ import annotations

from enum import StrEnum


class DomainErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_API_KEY = "invalid_api_key"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    REQUEST_TIMEOUT = "request_timeout"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UPSTREAM_ERROR = "upstream_error"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    UNAUTHORIZED = "unauthorized"
    VALIDATION_FAILED = "validation_failed"
    INVALID_INVITATION = "invalid_invitation"


class DomainError(Exception):
    """带稳定错误码的领域异常。"""

    def __init__(
        self,
        code: DomainErrorCode,
        message: str = "",
        *,
        status_code: int = 500,
    ) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.message = message
        self.status_code = status_code

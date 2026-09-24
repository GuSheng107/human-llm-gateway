"""推理 API（/v1/*）错误适配。

协议层把领域错误映射为 OpenAI / Anthropic 兼容的 HTTP 状态和 JSON
错误，不把人工、IM、fallback、内部路径或异常堆栈泄露给调用方
（docs/API_CONTRACT.md §16）。
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from ..domain.errors import DomainError, DomainErrorCode

# 领域错误码 -> (HTTP 状态, OpenAI type, OpenAI code)
_OPENAI_MAPPING: dict[DomainErrorCode, tuple[int, str, str]] = {
    DomainErrorCode.INVALID_REQUEST: (400, "invalid_request_error", "invalid_request"),
    DomainErrorCode.UNSUPPORTED_PARAMETER: (400, "invalid_request_error", "unsupported_parameter"),
    DomainErrorCode.CONTEXT_LENGTH_EXCEEDED: (
        400,
        "invalid_request_error",
        "context_length_exceeded",
    ),
    DomainErrorCode.PAYLOAD_TOO_LARGE: (413, "invalid_request_error", "payload_too_large"),
    DomainErrorCode.INVALID_API_KEY: (401, "invalid_request_error", "invalid_api_key"),
    DomainErrorCode.MODEL_NOT_FOUND: (404, "invalid_request_error", "model_not_found"),
    DomainErrorCode.RATE_LIMIT_EXCEEDED: (429, "rate_limit_error", "rate_limit_exceeded"),
    DomainErrorCode.REQUEST_TIMEOUT: (504, "timeout_error", "request_timeout"),
    DomainErrorCode.UPSTREAM_ERROR: (500, "server_error", "upstream_error"),
}

# 领域错误码 -> (HTTP 状态, Anthropic type)
_ANTHROPIC_MAPPING: dict[DomainErrorCode, tuple[int, str]] = {
    DomainErrorCode.INVALID_REQUEST: (400, "invalid_request_error"),
    DomainErrorCode.UNSUPPORTED_PARAMETER: (400, "invalid_request_error"),
    DomainErrorCode.CONTEXT_LENGTH_EXCEEDED: (400, "invalid_request_error"),
    DomainErrorCode.PAYLOAD_TOO_LARGE: (413, "request_too_large"),
    DomainErrorCode.INVALID_API_KEY: (401, "authentication_error"),
    DomainErrorCode.MODEL_NOT_FOUND: (404, "not_found_error"),
    DomainErrorCode.RATE_LIMIT_EXCEEDED: (429, "rate_limit_error"),
    DomainErrorCode.REQUEST_TIMEOUT: (504, "api_error"),
    DomainErrorCode.UPSTREAM_ERROR: (500, "api_error"),
}

_GENERIC_500 = (500, "server_error")

# 领域错误码 -> (默认 HTTP 状态, jev code)。jev 官方以 422 表示校验失败。
_SYSTEMONE_MAPPING: dict[DomainErrorCode, tuple[int, str]] = {
    DomainErrorCode.INVALID_REQUEST: (422, "unprocessable_entity"),
    DomainErrorCode.UNSUPPORTED_PARAMETER: (400, "unsupported_parameter"),
    DomainErrorCode.CONTEXT_LENGTH_EXCEEDED: (422, "context_length_exceeded"),
    DomainErrorCode.PAYLOAD_TOO_LARGE: (413, "payload_too_large"),
    DomainErrorCode.INVALID_API_KEY: (401, "unauthorized"),
    DomainErrorCode.MODEL_NOT_FOUND: (404, "model_not_found"),
    DomainErrorCode.RATE_LIMIT_EXCEEDED: (429, "rate_limit_exceeded"),
    DomainErrorCode.REQUEST_TIMEOUT: (504, "request_timeout"),
    DomainErrorCode.UPSTREAM_ERROR: (500, "upstream_error"),
    DomainErrorCode.VALIDATION_FAILED: (422, "unprocessable_entity"),
}


def openai_error_body(
    message: str, *, error_type: str, code: str | None, param: str | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def anthropic_error_body(message: str, *, error_type: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def openai_error_response(
    message: str,
    *,
    error_type: str,
    code: str | None,
    param: str | None = None,
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=openai_error_body(message, error_type=error_type, code=code, param=param),
    )


def anthropic_error_response(
    message: str, *, error_type: str, status_code: int = 400, request_id: str = ""
) -> JSONResponse:
    body = anthropic_error_body(message, error_type=error_type)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=body)


def map_domain_error_openai(exc: DomainError) -> tuple[int, str, str]:
    return _OPENAI_MAPPING.get(exc.code, (*_GENERIC_500, "internal_error"))


def openai_domain_error_response(exc: DomainError) -> JSONResponse:
    """把领域错误转为 OpenAI 兼容响应；未映射的错误不泄露内部消息。"""
    mapping = _OPENAI_MAPPING.get(exc.code)
    if mapping is None:
        return openai_error_response(
            "服务暂时不可用",
            error_type="server_error",
            code="internal_error",
            status_code=500,
        )
    status, error_type, code = mapping
    return openai_error_response(
        exc.message or "请求无法处理",
        error_type=error_type,
        code=exc.public_code or code,
        status_code=status,
    )


def anthropic_domain_error_response(exc: DomainError, *, request_id: str = "") -> JSONResponse:
    """把领域错误转为 Anthropic 兼容响应；未映射的错误不泄露内部消息。"""
    mapping = _ANTHROPIC_MAPPING.get(exc.code)
    if mapping is None:
        return anthropic_error_response(
            "Internal server error",
            error_type="api_error",
            status_code=500,
            request_id=request_id,
        )
    status, error_type = mapping
    return anthropic_error_response(
        exc.message or "Request could not be processed",
        error_type=error_type,
        status_code=status,
        request_id=request_id,
    )


def map_domain_error_anthropic(exc: DomainError) -> tuple[int, str]:
    status, error_type = _ANTHROPIC_MAPPING.get(exc.code, (500, "api_error"))
    return status, error_type


def systemone_error_body(message: str, *, code: str) -> dict[str, Any]:
    """jev 官方错误体：`{"error": {"code", "message"}}`。"""
    return {"error": {"code": code, "message": message}}


def systemone_domain_error_response(exc: DomainError) -> JSONResponse:
    """把领域错误转为 jev（TypeSafe System One）兼容响应。

    官方以 422 表示请求校验失败；raise 站点已显式声明目标状态码，故优先
    采用 ``exc.status_code``，仅在缺省时回落到错误码映射。未映射的错误不
    泄露内部消息。
    """
    mapping = _SYSTEMONE_MAPPING.get(exc.code)
    if mapping is None:
        return JSONResponse(
            status_code=500,
            content=systemone_error_body("服务暂时不可用", code="internal_error"),
        )
    default_status, default_code = mapping
    status = exc.status_code if 400 <= exc.status_code < 600 else default_status
    return JSONResponse(
        status_code=status,
        content=systemone_error_body(
            exc.message or "请求无法处理", code=exc.public_code or default_code
        ),
    )


def map_domain_error_systemone(exc: DomainError) -> tuple[int, str]:
    default_status, default_code = _SYSTEMONE_MAPPING.get(exc.code, (500, "internal_error"))
    status = exc.status_code if 400 <= exc.status_code < 600 else default_status
    return status, exc.public_code or default_code

"""管理 API 统一错误结构、request_id 中间件与异常映射。"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from ..domain.errors import DomainError, DomainErrorCode

logger = logging.getLogger("app.api")


class ApiErrorCode(StrEnum):
    AUTH_EXPIRED = "auth_expired"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION_FAILED = "validation_failed"
    SCHEMA_ERROR = "schema_error"
    INVALID_INVITATION = "invalid_invitation"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INTERNAL_ERROR = "internal_error"


class ApiErrorAction(StrEnum):
    RELOGIN = "relogin"
    FIX_INPUT = "fix_input"
    RETRY = "retry"
    VIEW_LOGS = "view_logs"
    NONE = "none"


# 领域错误码 -> 管理 API (HTTP 状态, 错误码, action)
_DOMAIN_MAPPING: dict[DomainErrorCode, tuple[int, ApiErrorCode, ApiErrorAction]] = {
    DomainErrorCode.UNAUTHORIZED: (401, ApiErrorCode.AUTH_EXPIRED, ApiErrorAction.RELOGIN),
    DomainErrorCode.FORBIDDEN: (403, ApiErrorCode.FORBIDDEN, ApiErrorAction.NONE),
    DomainErrorCode.NOT_FOUND: (404, ApiErrorCode.NOT_FOUND, ApiErrorAction.NONE),
    DomainErrorCode.CONFLICT: (409, ApiErrorCode.CONFLICT, ApiErrorAction.NONE),
    DomainErrorCode.VALIDATION_FAILED: (
        422,
        ApiErrorCode.VALIDATION_FAILED,
        ApiErrorAction.FIX_INPUT,
    ),
    DomainErrorCode.INVALID_INVITATION: (
        400,
        ApiErrorCode.INVALID_INVITATION,
        ApiErrorAction.FIX_INPUT,
    ),
    DomainErrorCode.RATE_LIMIT_EXCEEDED: (
        429,
        ApiErrorCode.RATE_LIMIT_EXCEEDED,
        ApiErrorAction.RETRY,
    ),
}


class ApiError(Exception):
    """统一管理 API 业务异常：code + message + action + details。"""

    def __init__(
        self,
        code: ApiErrorCode,
        message: str,
        *,
        status_code: int,
        action: ApiErrorAction = ApiErrorAction.NONE,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value
        self.message = message
        self.status_code = status_code
        self.action = action.value
        self.details = details or {}
        super().__init__(message)


def error_body(
    code: str,
    message: str,
    action: str,
    details: dict[str, Any] | None,
    request_id: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "action": action,
            "details": details or {},
            "request_id": request_id,
        }
    }


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        from ..core.logging import reset_request_id, set_request_id

        request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:24]}"
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            reset_request_id(token)


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status, code, action = _DOMAIN_MAPPING.get(
            exc.code, (500, ApiErrorCode.INTERNAL_ERROR, ApiErrorAction.VIEW_LOGS)
        )
        if 400 <= exc.status_code < 600:
            status = exc.status_code
        return JSONResponse(
            status_code=status,
            content=error_body(code.value, exc.message, action.value, {}, get_request_id(request)),
        )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                exc.code, exc.message, exc.action, exc.details, get_request_id(request)
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {key: item[key] for key in ("type", "loc", "msg") if key in item}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                ApiErrorCode.SCHEMA_ERROR.value,
                "请求参数校验失败",
                ApiErrorAction.FIX_INPUT.value,
                {"errors": errors},
                get_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_internal(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled request error",
            extra={"request_id": get_request_id(request), "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content=error_body(
                ApiErrorCode.INTERNAL_ERROR.value,
                "服务器内部错误",
                ApiErrorAction.VIEW_LOGS.value,
                {},
                get_request_id(request),
            ),
        )

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from ..enums import ErrorAction, ErrorCode

DEFAULT_STATUS_BY_CODE: dict[str, int] = {
    ErrorCode.AUTH_EXPIRED.value: 401,
    ErrorCode.FORBIDDEN.value: 403,
    ErrorCode.NOT_FOUND.value: 404,
    ErrorCode.CONFLICT.value: 409,
    ErrorCode.BINDING_LOCKED.value: 423,
    ErrorCode.VALIDATION_FAILED.value: 422,
    ErrorCode.RATE_LIMITED.value: 429,
    ErrorCode.HUMAN_TIMEOUT.value: 504,
    ErrorCode.LLM_ERROR.value: 502,
    ErrorCode.UPSTREAM_UNAVAILABLE.value: 502,
    ErrorCode.CONNECTOR_ERROR.value: 502,
    ErrorCode.INTERNAL_ERROR.value: 500,
}

logger = logging.getLogger("app.api")


class ApiError(Exception):
    """统一业务异常：code + message + action + details。"""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        status_code: int | None = None,
        action: ErrorAction | str = ErrorAction.NONE,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message
        self.status_code = (
            status_code if status_code is not None else DEFAULT_STATUS_BY_CODE.get(self.code, 400)
        )
        self.action = action.value if isinstance(action, ErrorAction) else str(action)
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
    """为每个请求注入 request_id，写入响应头并挂到 request.state。"""

    async def dispatch(self, request: StarletteRequest, call_next):
        request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:24]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def install_error_handlers(app: FastAPI) -> None:
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
        return JSONResponse(
            status_code=422,
            content=error_body(
                ErrorCode.VALIDATION_FAILED.value,
                "请求参数校验失败",
                ErrorAction.FIX_INPUT.value,
                {"errors": exc.errors()},
                get_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_internal(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled request error",
            extra={
                "request_id": get_request_id(request),
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=500,
            content=error_body(
                ErrorCode.INTERNAL_ERROR.value,
                "服务器内部错误",
                ErrorAction.VIEW_LOGS.value,
                {},
                get_request_id(request),
            ),
        )

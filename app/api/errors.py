"""管理 API 统一错误结构、request_id 中间件与异常映射。"""

from __future__ import annotations

import json
import logging
import uuid
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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


class RequestIdMiddleware:
    """纯 ASGI 实现：注入 request_id（scope.state + contextvar）并回写响应头。

    - 全部响应追加 ``X-Trace-Id``（与 request_id 同源）。
    - ``/api/*`` JSON 响应体顶层追加 ``trace_id`` 字段；``/v1/*`` 不改变
      OpenAI/Anthropic 协议正文。
    不能使用 BaseHTTPMiddleware：它会拦截 receive 通道，使
    request.is_disconnected() 在等待阶段永远返回 False，/v1/* 的
    调用方断开取消随之失效。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from ..core.logging import reset_request_id, set_request_id

        request_id = ""
        for key, value in scope.get("headers") or []:
            if key == b"x-request-id":
                request_id = value.decode("latin-1")
                break
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:24]}"
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        header_value = request_id.encode("latin-1")
        path: str = scope.get("path", "")
        is_admin_api = path.startswith("/api/")

        async def send_with_request_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [*(message.get("headers") or [])]
                # 移除可能已存在的同名头，避免重复。
                headers = [h for h in headers if h[0] not in (b"x-request-id", b"x-trace-id")]
                headers.append((b"x-request-id", header_value))
                headers.append((b"x-trace-id", header_value))
                message = {**message, "headers": headers}
            elif is_admin_api and message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body[:1] == b"{" and not message.get("more_body", False):
                    try:
                        payload = json.loads(body)
                        if isinstance(payload, dict) and "trace_id" not in payload:
                            payload["trace_id"] = request_id
                            new_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                            headers = [
                                (k, v)
                                for k, v in message.get("headers") or []
                                if k != b"content-length"
                            ]
                            message = {**message, "body": new_body, "headers": headers}
                    except (ValueError, TypeError):
                        pass
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        # /v1/* 使用 OpenAI 兼容错误结构；管理 API 使用统一错误结构。
        if request.url.path.startswith("/v1/"):
            # Anthropic Messages 使用 Anthropic 错误结构；其余 /v1 走 OpenAI 结构。
            if request.url.path.startswith("/v1/messages"):
                from ..protocols.errors import anthropic_domain_error_response

                return anthropic_domain_error_response(exc, request_id=get_request_id(request))
            from ..protocols.errors import openai_domain_error_response

            return openai_domain_error_response(exc)
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

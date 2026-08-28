"""应用工厂：装配路由、异常处理、request_id 中间件、lifespan 与静态挂载。

M2 只装配认证与健康检查；连接器运行时（M4）、推理协议（M6）等后续里程碑接入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .account import router as account_router
from .auth import router as auth_router
from .errors import RequestIdMiddleware, install_error_handlers
from .invitations import router as invitations_router
from .users import router as users_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from ..core.config import get_settings
        from ..core.db import SessionLocal
        from ..services.bootstrap import BootstrapService

        with SessionLocal() as db:
            BootstrapService().initialize(db, get_settings())
        yield

    app = FastAPI(title="Human LLM Gateway", version="0.3.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(invitations_router)
    app.include_router(users_router)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        from ..core.config import get_settings

        return {"status": "ok", "service": get_settings().app_name}

    admin_dist = Path(__file__).resolve().parent.parent.parent / "admin" / "dist"
    if admin_dist.is_dir():
        from fastapi.responses import FileResponse, JSONResponse

        app.mount("/", StaticFiles(directory=admin_dist, html=True), name="admin")

        @app.exception_handler(404)
        async def spa_fallback(request, exc):
            from .errors import ApiErrorAction, ApiErrorCode, get_request_id

            index = admin_dist / "index.html"
            if index.is_file() and not request.url.path.startswith(
                ("/api/", "/v1/", "/connectors/", "/docs", "/openapi")
            ):
                return FileResponse(index)
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": ApiErrorCode.NOT_FOUND.value,
                        "message": "资源不存在",
                        "action": ApiErrorAction.NONE.value,
                        "details": {"path": str(request.url.path)},
                        "request_id": get_request_id(request),
                    }
                },
            )

    return app


app = create_app()

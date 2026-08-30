"""应用工厂：装配路由、异常处理、request_id 中间件、lifespan 与静态挂载。

M4 接入连接器运行时；M5 接入 Fake Model 目录、模型分组、API Key 与
/v1/models；推理协议端点在 M6 接入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .account import router as account_router
from .api_keys import router as api_keys_router
from .assistant import router as assistant_router
from .auth import router as auth_router
from .connections import platforms_router
from .connections import router as connections_router
from .connectors import router as connectors_router
from .errors import RequestIdMiddleware, install_error_handlers
from .fake_models import groups_router
from .fake_models import router as fake_models_router
from .inference import router as inference_router
from .invitations import router as invitations_router
from .limits import BodySizeLimitMiddleware
from .llm_configs import router as llm_configs_router
from .logs import router as logs_router
from .tasks import router as tasks_router
from .tools import router as tools_router
from .users import router as users_router
from .v1_models import router as v1_models_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from ..connectors import connection_manager as manager
        from ..core.config import get_settings
        from ..core.db import SessionLocal
        from ..services.bootstrap import BootstrapService
        from ..services.connection_service import ConnectionService

        with SessionLocal() as db:
            BootstrapService().initialize(db, get_settings())
        # 连接器运行时装配与启动恢复（desired_running 的连接重新拉起）。
        service = ConnectionService()
        manager.set_state_recorder(service.runtime_state_recorder())
        with SessionLocal() as db:
            rows = service.repo.list_desired_running(db)
            snapshot = [(row, service.decrypt_config(row)) for row in rows]
        for row, config in snapshot:
            await manager.start(row, config, service.inbound_handler())
        yield
        # 优雅关闭：停止全部连接器实例。
        await manager.stop_all()

    app = FastAPI(title="Human LLM Gateway", version="0.6.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    # 请求体大小上限必须在鉴权与解析之前生效，因此注册在最外层。
    app.add_middleware(BodySizeLimitMiddleware)
    install_error_handlers(app)

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(invitations_router)
    app.include_router(users_router)
    app.include_router(platforms_router)
    app.include_router(connections_router)
    app.include_router(connectors_router)
    app.include_router(fake_models_router)
    app.include_router(groups_router)
    app.include_router(api_keys_router)
    app.include_router(tasks_router)
    app.include_router(v1_models_router)
    app.include_router(llm_configs_router)
    app.include_router(assistant_router)
    app.include_router(logs_router)
    app.include_router(tools_router)
    app.include_router(inference_router)

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

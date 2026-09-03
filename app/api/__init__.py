"""应用工厂：装配路由、异常处理、request_id 中间件、lifespan 与静态挂载。

M4 接入连接器运行时；M5 接入 Fake Model 目录、模型分组、API Key 与
/v1/models；推理协议端点在 M6 接入。
"""

from __future__ import annotations

import asyncio
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
from .connections import admin_router as admin_connections_router
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
from .mcp import router as mcp_router
from .tasks import router as tasks_router
from .tools import router as tools_router
from .users import router as users_router
from .v1_models import router as v1_models_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from ..connectors import connection_manager as manager
        from ..connectors.registry import default_registry
        from ..core.config import get_settings
        from ..core.db import SessionLocal
        from ..core.readiness import protocols_ready
        from ..services.bootstrap import BootstrapService
        from ..services.connection_service import ConnectionService
        from ..services.connection_watchdog import connection_watchdog
        from ..services.data_retention import data_retention
        from ..services.task_sweeper import task_sweeper

        with SessionLocal() as db:
            BootstrapService().initialize(db, get_settings())
        readiness = application.state.readiness
        readiness.mark_bootstrap_complete()
        # 结构化日志异步落库线程 + 普通 logging 告警接入 app_logs。
        from ..core.logging import install_persistence, stop_log_persistence

        install_persistence()
        # 连接器运行时装配与启动恢复（desired_running 的连接重新拉起）。
        service = ConnectionService()
        manager.set_state_recorder(service.runtime_state_recorder())
        with SessionLocal() as db:
            rows = service.repo.list_desired_running(db)
        await service.bootstrap_recover(rows)
        watchdog_task = asyncio.create_task(connection_watchdog.run(), name="connection-watchdog")
        # 僵尸任务收敛：等待循环消失（进程重启/断开未检测）后的兜底驱动。
        sweeper_task = asyncio.create_task(task_sweeper.run(), name="task-sweeper")
        retention_task = asyncio.create_task(data_retention.run(), name="data-retention")
        readiness.mark_runtime_started(
            tasks=(watchdog_task, sweeper_task, retention_task),
            protocols_ready=protocols_ready(),
            connector_registry_ready=bool(default_registry.list_specs()),
        )
        try:
            yield
        finally:
            readiness.reset()
            watchdog_task.cancel()
            sweeper_task.cancel()
            retention_task.cancel()
            for background in (watchdog_task, sweeper_task, retention_task):
                try:
                    await background
                except asyncio.CancelledError:
                    pass
            # 优雅关闭：停止全部连接器实例，并刷完日志队列。
            await manager.stop_all()
            stop_log_persistence()

    app = FastAPI(title="Human LLM Gateway", version="0.6.0", lifespan=lifespan)
    from ..core.readiness import ReadinessState

    app.state.readiness = ReadinessState()
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
    app.include_router(admin_connections_router)
    app.include_router(connectors_router)
    app.include_router(fake_models_router)
    app.include_router(groups_router)
    app.include_router(api_keys_router)
    app.include_router(tasks_router)
    app.include_router(v1_models_router)
    app.include_router(llm_configs_router)
    app.include_router(assistant_router)
    app.include_router(logs_router)
    app.include_router(mcp_router)
    app.include_router(tools_router)
    app.include_router(inference_router)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        from ..core.config import get_settings

        return {"status": "ok", "service": get_settings().app_name}

    @app.get("/readyz")
    def readiness() -> Any:
        from fastapi.responses import JSONResponse

        from ..core.config import get_settings

        payload = app.state.readiness.snapshot(get_settings().app_name)
        status_code = 200 if payload["status"] == "ready" else 503
        return JSONResponse(status_code=status_code, content=payload)

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

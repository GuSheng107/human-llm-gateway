"""应用工厂：装配所有 Router、异常处理、request_id 中间件、lifespan 与静态挂载。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api_keys import router as api_keys_router
from .auth import router as auth_router
from .connections import router as connections_router
from .connectors_ep import router as connectors_router
from .errors import RequestIdMiddleware, install_error_handlers
from .inference import router as inference_router
from .logs import router as logs_router
from .providers import router as providers_router
from .routes import router as routes_router
from .settings import router as settings_router
from .tasks import router as tasks_router
from .users import router as users_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from ..config import get_settings
        from ..connectors import ConnectorManager
        from ..db import SessionLocal, init_db
        from ..dblog import install_db_log_handler, log_event, remove_db_log_handler
        from ..inbound import InboundProcessor
        from ..model_catalog import seed_public_models
        from ..models import IMConnection
        from ..services import seed_admin

        init_db()
        db_handler = install_db_log_handler()
        log_event("info", "app", "服务启动", {"app": get_settings().app_name})
        manager = ConnectorManager()
        application.state.connector_manager = manager

        inbound_processor = InboundProcessor(get_settings(), manager)

        async def handle_inbound(message: Any) -> None:
            with SessionLocal() as inbound_db:
                await inbound_processor.handle(inbound_db, message)

        manager.set_on_message(handle_inbound)
        with SessionLocal() as db:
            settings = get_settings()
            seed_admin(db, settings.admin_username, settings.admin_password)
            seed_public_models(db)
        with SessionLocal() as startup_db:
            connections = list(
                startup_db.execute(
                    select(IMConnection).where(IMConnection.deleted_at.is_(None))
                ).scalars()
            )
        await manager.start_all(connections)
        yield
        await manager.stop_all()
        log_event("info", "app", "服务关闭", {})
        remove_db_log_handler(db_handler)

    app = FastAPI(title="Human LLM Gateway", version="0.2.0", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    for module_router in (
        auth_router,
        users_router,
        connections_router,
        providers_router,
        routes_router,
        api_keys_router,
        tasks_router,
        logs_router,
        settings_router,
        inference_router,
        connectors_router,
    ):
        app.include_router(module_router)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        from ..config import get_settings

        return {"status": "ok", "service": get_settings().app_name}

    admin_dist = Path(__file__).resolve().parent.parent.parent / "admin" / "dist"
    if admin_dist.is_dir():
        from fastapi.responses import FileResponse, JSONResponse

        app.mount("/", StaticFiles(directory=admin_dist, html=True), name="admin")

        @app.exception_handler(404)
        async def spa_fallback(request, exc):
            from .errors import ErrorAction, ErrorCode, get_request_id

            index = admin_dist / "index.html"
            if index.is_file() and not request.url.path.startswith(
                ("/api/", "/v1/", "/connectors/", "/docs", "/openapi")
            ):
                return FileResponse(index)
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": ErrorCode.NOT_FOUND.value,
                        "message": "资源不存在",
                        "action": ErrorAction.NONE.value,
                        "details": {"path": str(request.url.path)},
                        "request_id": get_request_id(request),
                    }
                },
            )

    return app


app = create_app()

import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import Settings, get_settings
from .connection_config import load_connection_config
from .connectors import ConnectorManager, connector_registry
from .connectors.base import InboundMessage
from .db import get_db
from .dsl import ParsedEvent
from .enums import ConnectorPlatform, ReplySource, RouteMode, UserRole
from .im_connections import (
    connection_summary,
    create_user_connection,
    get_managed_connection,
    list_connections,
    soft_delete_connection,
    start_binding,
)
from .inbound import InboundProcessor
from .model_catalog import list_public_models, seed_public_models
from .models import (
    AdminUser,
    ApiKey,
    AppLog,
    AuditLog,
    HumanOperator,
    IMConnection,
    LLMModel,
    LLMProvider,
    ModelRoute,
    PublicModel,
    RequestTask,
)
from .protocols import (
    anthropic_json,
    anthropic_stream,
    openai_chat_json,
    openai_chat_stream,
    openai_responses_json,
    openai_responses_stream,
)
from .schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeySummary,
    ConnectionCreate,
    ConnectionCreated,
    ConnectionSummary,
    CurrentUserSummary,
    HumanReplyRequest,
    LoginRequest,
    LoginResponse,
    ProviderCreate,
    ProviderSummary,
    PublicModelCreate,
    PublicModelSummary,
    PublicModelUpdate,
    RouteCreate,
    RouteSummary,
    TaskSummary,
    UserCreate,
)
from .security import (
    encrypt_secret,
    generate_api_key,
    hash_password,
    issue_admin_token,
    verify_admin_token,
    verify_password,
)
from .services import TaskError, TaskService, find_api_key, seed_admin, task_to_dict

bearer = HTTPBearer(auto_error=False)


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return str(value or "")


def openai_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail={"type": "invalid_request_error",
                                                       "message": "messages 不能为空"})
    return [{"role": str(item.get("role", "user")), "content": _text_content(item.get("content"))}
            for item in messages if isinstance(item, dict)]


def openai_responses_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_input = payload.get("input")
    if isinstance(raw_input, str) and raw_input.strip():
        return [{"role": "user", "content": raw_input}]
    if not isinstance(raw_input, list) or not raw_input:
        raise HTTPException(
            status_code=400,
            detail={"type": "invalid_request_error", "message": "input 不能为空"},
        )
    messages: list[dict[str, Any]] = []
    for item in raw_input:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "message"))
        if item_type not in {"message", "input_text"}:
            continue
        content = item.get("content", item.get("text", ""))
        messages.append(
            {"role": str(item.get("role", "user")), "content": _text_content(content)}
        )
    if not messages:
        raise HTTPException(
            status_code=400,
            detail={"type": "invalid_request_error", "message": "input 中没有可用消息"},
        )
    return messages


def anthropic_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail={"type": "invalid_request_error",
                                                       "message": "messages 不能为空"})
    normalized: list[dict[str, Any]] = []
    system = payload.get("system")
    if system:
        normalized.append({"role": "system", "content": _text_content(system)})
    normalized.extend({"role": str(item.get("role", "user")),
                       "content": _text_content(item.get("content"))}
                      for item in messages if isinstance(item, dict))
    return normalized


def route_model_names(route: ModelRoute) -> list[str]:
    try:
        configured = json.loads(route.allowed_models_json or "[]")
    except json.JSONDecodeError:
        configured = []
    names = [str(name) for name in configured if str(name).strip()] if isinstance(configured, list) else []
    return list(dict.fromkeys([route.model_name, *names]))


def get_task_service(request: Request, settings: Settings = Depends(get_settings)) -> TaskService:
    return TaskService(settings, getattr(request.app.state, "connector_manager", None))


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> AdminUser:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    username = verify_admin_token(credentials.credentials, settings.app_secret)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    user = db.execute(
        select(AdminUser).where(
            AdminUser.username == username,
            AdminUser.active.is_(True),
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return user


def require_admin(user: AdminUser = Depends(require_current_user)) -> str:
    if user.role is not UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user.username


def require_api_key(authorization: str | None = Header(default=None),
                    db: Session = Depends(get_db)) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"type": "invalid_api_key", "message": "缺少 API Key"})
    key = find_api_key(db, authorization[7:].strip())
    if not key:
        raise HTTPException(status_code=401, detail={"type": "invalid_api_key", "message": "API Key 无效"})
    return key


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from .db import SessionLocal, init_db
        from .dblog import install_db_log_handler, log_event, remove_db_log_handler

        init_db()
        # 把标准 logging 的 WARNING+ 桥接到 app_logs 表，运行时异常统一落库
        db_handler = install_db_log_handler()
        log_event("info", "app", "服务启动", {"app": get_settings().app_name})
        manager = ConnectorManager()
        application.state.connector_manager = manager

        inbound_processor = InboundProcessor(get_settings(), manager)

        async def handle_inbound(message: InboundMessage) -> None:
            from .db import SessionLocal

            with SessionLocal() as inbound_db:
                await inbound_processor.handle(inbound_db, message)

        manager.set_on_message(handle_inbound)
        with SessionLocal() as db:
            settings = get_settings()
            seed_admin(db, settings.admin_username, settings.admin_password)
            # 全新数据库首次启动时写入默认公开模型；幂等，管理员清空后不再补种
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

    app = FastAPI(title="Human LLM Gateway", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": get_settings().app_name}

    @app.post("/auth/login", response_model=LoginResponse)
    def login(payload: LoginRequest, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)) -> LoginResponse:
        user = db.execute(select(AdminUser).where(AdminUser.username == payload.username,
                                                   AdminUser.active.is_(True))).scalar_one_or_none()
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return LoginResponse(
            access_token=issue_admin_token(user.username, settings.app_secret),
            username=user.username,
            display_name=user.display_name or user.username,
            role=user.role,
        )

    @app.get("/auth/me", response_model=CurrentUserSummary)
    def current_user(user: AdminUser = Depends(require_current_user)) -> CurrentUserSummary:
        return CurrentUserSummary(
            id=user.id,
            username=user.username,
            display_name=user.display_name or user.username,
            role=user.role,
        )

    @app.post("/admin/users", response_model=CurrentUserSummary)
    def create_user(
        payload: UserCreate,
        db: Session = Depends(get_db),
        admin: str = Depends(require_admin),
    ) -> CurrentUserSummary:
        if payload.role is UserRole.ADMIN:
            raise HTTPException(status_code=400, detail="本接口只创建普通用户")
        if db.execute(
            select(AdminUser).where(AdminUser.username == payload.username)
        ).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="用户名已存在")
        user = AdminUser(
            username=payload.username,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
            role=UserRole.USER,
        )
        db.add(user)
        db.flush()
        db.add(
            AuditLog(
                action="user.created",
                subject_type="user",
                subject_id=str(user.id),
                actor=admin,
                detail_json=json.dumps({"username": user.username}, ensure_ascii=False),
            )
        )
        db.commit()
        return CurrentUserSummary(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        )

    @app.get("/v1/models")
    def models(key: ApiKey = Depends(require_api_key),
               db: Session = Depends(get_db)) -> dict[str, Any]:
        return {"object": "list", "data": [
            {"id": item.model_id, "object": "model", "owned_by": item.owned_by}
            for item in list_public_models(db)
        ]}

    def public_model_summary(item: PublicModel) -> PublicModelSummary:
        return PublicModelSummary(id=item.id, model_id=item.model_id, owned_by=item.owned_by,
                                  sort_order=item.sort_order, active=item.active)

    @app.get("/admin/models", response_model=list[PublicModelSummary])
    def list_admin_models(_: str = Depends(require_admin),
                          db: Session = Depends(get_db)) -> list[PublicModelSummary]:
        return [public_model_summary(item)
                for item in list_public_models(db, include_inactive=True)]

    @app.post("/admin/models", response_model=PublicModelSummary)
    def create_public_model(payload: PublicModelCreate, db: Session = Depends(get_db),
                            admin: str = Depends(require_admin)) -> PublicModelSummary:
        if db.execute(select(PublicModel).where(
                PublicModel.model_id == payload.model_id)).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="模型 ID 已存在")
        item = PublicModel(model_id=payload.model_id, owned_by=payload.owned_by,
                           sort_order=payload.sort_order, active=payload.active)
        db.add(item)
        db.flush()
        db.add(AuditLog(action="public_model.created", subject_type="public_model",
                        subject_id=str(item.id), actor=admin,
                        detail_json=json.dumps({"model_id": item.model_id}, ensure_ascii=False)))
        db.commit()
        return public_model_summary(item)

    @app.put("/admin/models/{model_id}", response_model=PublicModelSummary)
    def update_public_model(model_id: int, payload: PublicModelUpdate,
                            db: Session = Depends(get_db),
                            admin: str = Depends(require_admin)) -> PublicModelSummary:
        item = db.get(PublicModel, model_id)
        if item is None:
            raise HTTPException(status_code=404, detail="公开模型不存在")
        if payload.model_id is not None and payload.model_id != item.model_id:
            if db.execute(select(PublicModel).where(
                    PublicModel.model_id == payload.model_id)).scalar_one_or_none():
                raise HTTPException(status_code=409, detail="模型 ID 已存在")
            item.model_id = payload.model_id
        if payload.owned_by is not None:
            item.owned_by = payload.owned_by
        if payload.sort_order is not None:
            item.sort_order = payload.sort_order
        if payload.active is not None:
            item.active = payload.active
        db.add(AuditLog(action="public_model.updated", subject_type="public_model",
                        subject_id=str(item.id), actor=admin,
                        detail_json=json.dumps(
                            payload.model_dump(exclude_none=True), ensure_ascii=False)))
        db.commit()
        return public_model_summary(item)

    @app.delete("/admin/models/{model_id}")
    def delete_public_model(model_id: int, db: Session = Depends(get_db),
                            admin: str = Depends(require_admin)) -> dict[str, Any]:
        item = db.get(PublicModel, model_id)
        if item is None:
            raise HTTPException(status_code=404, detail="公开模型不存在")
        db.add(AuditLog(action="public_model.deleted", subject_type="public_model",
                        subject_id=str(item.id), actor=admin,
                        detail_json=json.dumps({"model_id": item.model_id}, ensure_ascii=False)))
        db.delete(item)
        db.commit()
        return {"deleted": True}

    @app.post("/admin/api-keys", response_model=ApiKeyCreated)
    def create_key(payload: ApiKeyCreate, db: Session = Depends(get_db),
                   admin: str = Depends(require_admin)) -> ApiKeyCreated:
        secret, prefix, secret_hash = generate_api_key()
        operator = db.get(HumanOperator, payload.human_operator_id) if payload.human_operator_id else None
        if operator is None:
            operator = HumanOperator(display_name=payload.operator_name, status="offline")
            db.add(operator)
            db.flush()
        connection = db.get(IMConnection, payload.im_connection_id)
        if connection is None or connection.deleted_at is not None:
            raise HTTPException(status_code=404, detail="IM Bot 不存在")
        route = db.get(ModelRoute, payload.route_id) if payload.route_id else None
        if route is None:
            route = ModelRoute(name=payload.route_name, model_name=payload.model_name,
                               upstream_model=payload.model_name, mode=payload.route_mode,
                               provider_id=payload.provider_id,
                               human_timeout_seconds=payload.human_timeout_seconds,
                               allowed_models_json=json.dumps([payload.model_name], ensure_ascii=False))
            db.add(route)
            db.flush()
        if route.mode in {RouteMode.LLM, RouteMode.HUMAN_FALLBACK_LLM} and route.provider_id is None:
            raise HTTPException(status_code=400, detail="LLM 或 fallback 路由必须配置供应商")
        if operator.api_key or connection.api_key:
            raise HTTPException(status_code=409, detail="真人或 IM 连接已绑定其它 API Key")
        key = ApiKey(name=payload.name, prefix=prefix, secret_hash=secret_hash,
                     human_operator_id=operator.id, im_connection_id=connection.id,
                     route_id=route.id)
        db.add(key)
        db.add(AuditLog(action="api_key.created", subject_type="api_key", subject_id="new",
                        actor=admin, detail_json=json.dumps({"prefix": prefix})))
        db.commit()
        return ApiKeyCreated(id=key.id, name=key.name, prefix=key.prefix, active=key.active,
                             operator_name=operator.display_name, im_name=connection.name,
                             platform=connection.platform, route_mode=route.mode,
                             model_name=route.model_name, secret=secret)

    @app.post("/admin/providers", response_model=ProviderSummary)
    def create_provider(payload: ProviderCreate, db: Session = Depends(get_db),
                        admin: str = Depends(require_admin)) -> ProviderSummary:
        if payload.protocol not in {"openai_compatible", "anthropic"}:
            raise HTTPException(status_code=400, detail="供应商协议只支持 openai_compatible 或 anthropic")
        provider = LLMProvider(name=payload.name, protocol=payload.protocol,
                               base_url=payload.base_url.rstrip("/"),
                               api_key_encrypted=encrypt_secret(payload.api_key, get_settings().app_secret)
                               if payload.api_key else "",
                               options_json=json.dumps(payload.options, ensure_ascii=False))
        db.add(provider)
        try:
            db.flush()
            db.add(AuditLog(action="provider.created", subject_type="llm_provider",
                            subject_id=str(provider.id), actor=admin,
                            detail_json=json.dumps({"name": provider.name}, ensure_ascii=False)))
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="供应商名称已存在或配置无效") from exc
        return ProviderSummary(id=provider.id, name=provider.name, protocol=provider.protocol,
                               base_url=provider.base_url, active=provider.active)

    @app.get("/admin/providers", response_model=list[ProviderSummary])
    def list_providers(_: str = Depends(require_admin), db: Session = Depends(get_db)) -> list[ProviderSummary]:
        return [ProviderSummary(id=p.id, name=p.name, protocol=p.protocol, base_url=p.base_url, active=p.active)
                for p in db.execute(select(LLMProvider).order_by(LLMProvider.id)).scalars()]

    @app.post("/admin/providers/{provider_id}/models/sync")
    async def sync_provider_models(provider_id: int, admin: str = Depends(require_admin),
                                   db: Session = Depends(get_db)) -> dict[str, Any]:
        provider = db.get(LLMProvider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="LLM 供应商不存在")
        try:
            models = await TaskService(get_settings()).llm.list_models(provider, get_settings().app_secret)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="获取上游模型列表失败") from exc
        for item in models:
            model_id = str(item["id"])
            catalog = db.execute(select(LLMModel).where(LLMModel.provider_id == provider.id,
                                                         LLMModel.model_id == model_id)).scalar_one_or_none()
            if catalog is None:
                catalog = LLMModel(provider_id=provider.id, model_id=model_id)
                db.add(catalog)
            catalog.owned_by = str(item.get("owned_by", ""))
            catalog.metadata_json = json.dumps(item, ensure_ascii=False)
            catalog.active = True
        db.add(AuditLog(action="provider.models_synced", subject_type="llm_provider",
                        subject_id=str(provider.id), actor=admin,
                        detail_json=json.dumps({"count": len(models)}, ensure_ascii=False)))
        db.commit()
        return {"object": "list", "provider_id": provider.id,
                "data": [{"id": item["id"], "object": item.get("object", "model"),
                           "owned_by": item.get("owned_by", "")} for item in models]}

    @app.get("/admin/providers/{provider_id}/models")
    def list_provider_models(provider_id: int, _: str = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict[str, Any]:
        if db.get(LLMProvider, provider_id) is None:
            raise HTTPException(status_code=404, detail="LLM 供应商不存在")
        models = db.execute(select(LLMModel).where(LLMModel.provider_id == provider_id,
                                                   LLMModel.active.is_(True))
                            .order_by(LLMModel.model_id)).scalars()
        return {"object": "list", "provider_id": provider_id,
                "data": [{"id": item.model_id, "object": "model", "owned_by": item.owned_by}
                         for item in models]}

    @app.post("/admin/routes", response_model=RouteSummary)
    def create_route(payload: RouteCreate, db: Session = Depends(get_db),
                     admin: str = Depends(require_admin)) -> RouteSummary:
        if payload.mode in {RouteMode.LLM, RouteMode.HUMAN_FALLBACK_LLM} and payload.provider_id is None:
            raise HTTPException(status_code=400, detail="LLM 或 fallback 路由必须配置供应商")
        if payload.provider_id is not None and db.get(LLMProvider, payload.provider_id) is None:
            raise HTTPException(status_code=404, detail="LLM 供应商不存在")
        route = ModelRoute(name=payload.name, model_name=payload.model_name,
                           upstream_model=payload.upstream_model or payload.model_name,
                           mode=payload.mode, provider_id=payload.provider_id,
                           human_timeout_seconds=payload.human_timeout_seconds,
                           allowed_models_json=json.dumps(
                               list(dict.fromkeys([payload.model_name, *payload.model_names])),
                               ensure_ascii=False))
        db.add(route)
        db.flush()
        db.add(AuditLog(action="route.created", subject_type="model_route",
                        subject_id=str(route.id), actor=admin, detail_json="{}"))
        db.commit()
        return RouteSummary(id=route.id, name=route.name, model_name=route.model_name,
                            upstream_model=route.upstream_model,
                            model_names=route_model_names(route),
                            mode=route.mode, provider_id=route.provider_id,
                            human_timeout_seconds=route.human_timeout_seconds)

    @app.get("/admin/routes", response_model=list[RouteSummary])
    def list_routes(_: str = Depends(require_admin), db: Session = Depends(get_db)) -> list[RouteSummary]:
        return [RouteSummary(id=r.id, name=r.name, model_name=r.model_name,
                             upstream_model=r.upstream_model,
                             model_names=route_model_names(r), mode=r.mode,
                             provider_id=r.provider_id, human_timeout_seconds=r.human_timeout_seconds)
                for r in db.execute(select(ModelRoute).order_by(ModelRoute.id)).scalars()]

    @app.get("/api/im-platforms")
    def list_im_platforms(_: AdminUser = Depends(require_current_user)) -> list[dict[str, Any]]:
        return [definition.public_dict() for definition in connector_registry.all()]

    @app.post("/api/im-connections", response_model=ConnectionCreated)
    async def create_connection(
        payload: ConnectionCreate,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> ConnectionCreated:
        connection, created = create_user_connection(
            db,
            user,
            name=payload.name,
            platform=payload.platform,
            raw_config=payload.config,
            registry=connector_registry,
        )
        await app.state.connector_manager.configure(connection)
        db.refresh(connection)
        return ConnectionCreated(
            **connection_summary(connection).model_dump(),
            setup=created.setup,
        )

    @app.get("/api/im-connections", response_model=list[ConnectionSummary])
    def list_user_connections(
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> list[ConnectionSummary]:
        return list_connections(db, user)

    @app.delete("/api/im-connections/{connector_id}")
    async def delete_connection(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        connection = get_managed_connection(db, user, connector_id)
        await app.state.connector_manager.stop_connection(connector_id)
        soft_delete_connection(db, user, connection)
        return {"deleted": True}

    @app.post("/api/im-connections/{connector_id}/start")
    async def start_connector(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        connection = get_managed_connection(db, user, connector_id)
        manager = app.state.connector_manager
        await manager.configure(connection)
        return await manager.health(connector_id)

    @app.post("/api/im-connections/{connector_id}/stop")
    async def stop_connector(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        get_managed_connection(db, user, connector_id)
        await app.state.connector_manager.stop_connection(connector_id)
        return {"stopped": True}

    @app.get("/api/im-connections/{connector_id}/health")
    async def connector_health(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        get_managed_connection(db, user, connector_id)
        return await app.state.connector_manager.health(connector_id)

    @app.post("/api/im-connections/{connector_id}/login")
    async def connector_login(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        connection = get_managed_connection(db, user, connector_id)
        if user.role is UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="管理员不能发起用户 Bot 登录")
        manager = app.state.connector_manager
        if manager.get(connector_id) is None:
            await manager.configure(connection)
        try:
            return await manager.login(connector_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/im-connections/{connector_id}/login")
    async def connector_login_state(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        connection = get_managed_connection(db, user, connector_id)
        if user.role is UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="管理员不能读取用户 Bot 登录凭据")
        manager = app.state.connector_manager
        connector = manager.get(connector_id)
        if connector is None:
            await manager.configure(connection)
            connector = manager.get(connector_id)
        if connector is None or not hasattr(connector, "login_snapshot"):
            raise HTTPException(status_code=400, detail="该平台无扫码登录流程")
        return connector.login_snapshot()

    @app.post("/api/im-connections/{connector_id}/binding")
    async def begin_connection_binding(
        connector_id: int,
        user: AdminUser = Depends(require_current_user),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        connection = get_managed_connection(db, user, connector_id)
        return start_binding(db, user, connection, get_settings()).model_dump()

    @app.get("/admin/api-keys", response_model=list[ApiKeySummary])
    def list_keys(_: str = Depends(require_admin), db: Session = Depends(get_db)) -> list[ApiKeySummary]:
        keys = db.execute(select(ApiKey).options(joinedload(ApiKey.human_operator),
                                                 joinedload(ApiKey.im_connection),
                                                 joinedload(ApiKey.route))).scalars().all()
        return [ApiKeySummary(id=k.id, name=k.name, prefix=k.prefix, active=k.active,
                              operator_name=k.human_operator.display_name, im_name=k.im_connection.name,
                              platform=k.im_connection.platform, route_mode=k.route.mode,
                              model_name=k.route.model_name) for k in keys]

    @app.get("/admin/tasks", response_model=list[TaskSummary])
    def list_tasks(_: str = Depends(require_admin), db: Session = Depends(get_db)) -> list[TaskSummary]:
        tasks = db.execute(select(RequestTask).order_by(RequestTask.created_at.desc())).scalars().all()
        return [TaskSummary(**task_to_dict(t)) for t in tasks]

    @app.get("/admin/tasks/{task_id}")
    def task_detail(task_id: str, _: str = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
        task = db.execute(select(RequestTask).where(RequestTask.id == task_id)
                          .options(joinedload(RequestTask.events))).unique().scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {**task_to_dict(task), "events": [{"sequence": e.sequence, "kind": e.kind.value,
                                                    "content": e.content, "tool_name": e.tool_name,
                                                    "tool_args": e.tool_args_json, "source": e.source.value,
                                                    "external_message_id": e.external_message_id}
                                                   for e in task.events]}

    @app.post("/admin/tasks/{task_id}/reply")
    def web_reply(task_id: str, payload: HumanReplyRequest, admin: str = Depends(require_admin),
                  db: Session = Depends(get_db), service: TaskService = Depends(get_task_service)) -> dict[str, Any]:
        try:
            events = service.accept_reply(db, task_id, payload.text, ReplySource.WEB, admin,
                                          payload.external_message_id)
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id, "accepted": True, "source": "web", "events": [e.kind.value for e in events]}

    @app.get("/admin/audit-logs")
    def list_audit_logs(_: str = Depends(require_admin), db: Session = Depends(get_db),
                        action: str | None = Query(default=None),
                        limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if action:
            stmt = select(AuditLog).where(AuditLog.action == action).order_by(
                AuditLog.created_at.desc()).limit(limit)
        return [{"id": a.id, "action": a.action, "subject_type": a.subject_type,
                 "subject_id": a.subject_id, "actor": a.actor, "detail": a.detail_json,
                 "created_at": a.created_at.isoformat()} for a in db.execute(stmt).scalars()]

    @app.get("/admin/app-logs")
    def list_app_logs(_: str = Depends(require_admin), db: Session = Depends(get_db),
                      level: str | None = Query(default=None),
                      logger: str | None = Query(default=None),
                      limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        stmt = select(AppLog).order_by(AppLog.created_at.desc()).limit(limit)
        if level or logger:
            conditions = []
            if level:
                conditions.append(AppLog.level == level)
            if logger:
                conditions.append(AppLog.logger == logger)
            stmt = select(AppLog).where(*conditions).order_by(
                AppLog.created_at.desc()).limit(limit)
        return [{"id": a.id, "level": a.level, "logger": a.logger, "message": a.message,
                 "detail": a.detail_json, "created_at": a.created_at.isoformat()}
                for a in db.execute(stmt).scalars()]

    async def complete_task(task: RequestTask, events: list[ParsedEvent], payload: dict[str, Any],
                            protocol: str, db: Session, service: TaskService) -> Any:
        on_complete = lambda: service.mark_completed(db, task.id)
        if payload.get("stream"):
            if protocol == "anthropic":
                stream = anthropic_stream(task, events, get_settings(), on_complete)
            elif protocol == "openai_responses":
                stream = openai_responses_stream(task, events, get_settings(), on_complete)
            else:
                stream = openai_chat_stream(task, events, get_settings(), on_complete)
            return StreamingResponse(stream, media_type="text/event-stream")
        service.mark_completed(db, task.id)
        if protocol == "anthropic":
            body = anthropic_json(task, events)
        elif protocol == "openai_responses":
            body = openai_responses_json(task, events)
        else:
            body = openai_chat_json(task, events)
        return JSONResponse(body)

    @app.post("/connectors/webhook/{connector_id}/inbound")
    async def webhook_inbound(connector_id: int, request: Request,
                              token: str = Header(default="", alias="X-Connector-Token"),
                              db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if (
            connection is None
            or connection.deleted_at is not None
            or connection.platform is not ConnectorPlatform.WEBHOOK
        ):
            raise HTTPException(status_code=404, detail="连接不存在")
        try:
            config = load_connection_config(connection.config_json)
        except (ValueError, TypeError):
            config = {}
        expected_token = str(config.get("inbound_token", ""))
        if not token or not expected_token or not hmac.compare_digest(token, expected_token):
            raise HTTPException(status_code=403, detail="连接 token 无效")
        payload = await request.json()
        text = str(payload.get("text", ""))
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        message = InboundMessage(
            connector_id=connector_id,
            sender_id=str(payload.get("sender_id", "")),
            text=text,
            conversation_id=str(payload.get("conversation_id", "")),
            external_message_id=str(payload.get("external_message_id", "")),
            reply_to_task_id=(
                str(payload["reply_to_task_id"]) if payload.get("reply_to_task_id") else None
            ),
        )
        await app.state.connector_manager.dispatch(message)
        return {"accepted": True}

    @app.websocket("/connectors/ws/{connector_id}")
    async def ws_connect(websocket: WebSocket, connector_id: int, token: str = Query(default="")) -> None:
        from .db import SessionLocal

        with SessionLocal() as db:
            connection = db.get(IMConnection, connector_id)
            valid = (
                connection is not None
                and connection.deleted_at is None
                and connection.platform is ConnectorPlatform.WEBSOCKET
            )
            expected_token = ""
            if valid:
                try:
                    config = load_connection_config(connection.config_json)
                    expected_token = str(config.get("auth_token", ""))
                except (ValueError, TypeError):
                    valid = False
        if (
            not valid
            or not expected_token
            or not hmac.compare_digest(token, expected_token)
        ):
            await websocket.close(code=4401)
            return
        manager = websocket.app.state.connector_manager
        connector = manager.get(connector_id)
        if connector is None:
            with SessionLocal() as db:
                conn = db.get(IMConnection, connector_id)
                if conn is not None:
                    await manager.configure(conn)
            connector = manager.get(connector_id)
        await websocket.accept()
        if connector is not None:
            await connector.register(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"text": raw}
                text = str(data.get("text", ""))
                if not text:
                    continue
                message = InboundMessage(
                    connector_id=connector_id,
                    sender_id=str(data.get("sender_id", "")),
                    text=text,
                    conversation_id=str(data.get("conversation_id", "")),
                    external_message_id=str(data.get("external_message_id", "")),
                    reply_to_task_id=(
                        str(data["reply_to_task_id"])
                        if data.get("reply_to_task_id")
                        else None
                    ),
                )
                await manager.dispatch(message)
        except WebSocketDisconnect:
            pass
        finally:
            if connector is not None:
                await connector.unregister(websocket)

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict[str, Any], key: ApiKey = Depends(require_api_key),
                               db: Session = Depends(get_db), service: TaskService = Depends(get_task_service)) -> Any:
        requested_model = str(payload.get("model") or key.route.model_name)
        # The caller's model is accepted for SDK compatibility but never
        # selects the upstream model; the administrator-owned route does.
        messages = openai_messages(payload)
        if key.route.mode is RouteMode.LLM:
            task = service.create_llm_task(db, key, "openai", requested_model, payload)
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as exc:
                raise HTTPException(status_code=502, detail={"type": "llm_error", "task_id": task.id,
                                                               "message": str(exc)}) from exc
            return await complete_task(task, events, payload, "openai", db, service)
        task = service.create_human_task(db, key, "openai", requested_model, payload)
        try:
            events = await service.await_human(task.id, key.route.human_timeout_seconds)
        except TaskError as exc:
            service.mark_timeout(db, task.id)
            if key.route.mode is not RouteMode.HUMAN_FALLBACK_LLM:
                raise HTTPException(status_code=504, detail={"type": "human_timeout", "task_id": task.id}) from exc
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as llm_exc:
                raise HTTPException(status_code=502, detail={"type": "fallback_llm_error", "task_id": task.id,
                                                               "message": str(llm_exc)}) from llm_exc
        return await complete_task(task, events, payload, "openai", db, service)

    @app.post("/v1/messages")
    async def anthropic_messages_endpoint(payload: dict[str, Any], key: ApiKey = Depends(require_api_key),
                                          db: Session = Depends(get_db),
                                          service: TaskService = Depends(get_task_service)) -> Any:
        requested_model = str(payload.get("model") or key.route.model_name)
        # Keep the same policy for Anthropic clients: the bound route wins.
        messages = anthropic_messages(payload)
        if key.route.mode is RouteMode.LLM:
            task = service.create_llm_task(db, key, "anthropic", requested_model, payload)
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as exc:
                raise HTTPException(status_code=502, detail={"type": "api_error", "message": str(exc),
                                                               "task_id": task.id}) from exc
            return await complete_task(task, events, payload, "anthropic", db, service)
        task = service.create_human_task(db, key, "anthropic", requested_model, payload)
        try:
            events = await service.await_human(task.id, key.route.human_timeout_seconds)
        except TaskError as exc:
            service.mark_timeout(db, task.id)
            if key.route.mode is not RouteMode.HUMAN_FALLBACK_LLM:
                raise HTTPException(status_code=504, detail={"type": "timeout_error", "task_id": task.id}) from exc
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as llm_exc:
                raise HTTPException(status_code=502, detail={"type": "api_error", "task_id": task.id,
                                                               "message": str(llm_exc)}) from llm_exc
        return await complete_task(task, events, payload, "anthropic", db, service)

    @app.post("/v1/responses")
    async def responses_endpoint(
        payload: dict[str, Any],
        key: ApiKey = Depends(require_api_key),
        db: Session = Depends(get_db),
        service: TaskService = Depends(get_task_service),
    ) -> Any:
        requested_model = str(payload.get("model") or key.route.model_name)
        messages = openai_responses_messages(payload)
        if key.route.mode is RouteMode.LLM:
            task = service.create_llm_task(
                db, key, "openai_responses", requested_model, payload
            )
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "type": "server_error",
                        "task_id": task.id,
                        "message": str(exc),
                    },
                ) from exc
            return await complete_task(
                task, events, payload, "openai_responses", db, service
            )
        task = service.create_human_task(
            db, key, "openai_responses", requested_model, payload
        )
        try:
            events = await service.await_human(task.id, key.route.human_timeout_seconds)
        except TaskError as exc:
            service.mark_timeout(db, task.id)
            if key.route.mode is not RouteMode.HUMAN_FALLBACK_LLM:
                raise HTTPException(
                    status_code=504,
                    detail={"type": "human_timeout", "task_id": task.id},
                ) from exc
            try:
                events = await service.complete_llm_task(db, task.id, messages)
            except TaskError as llm_exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "type": "server_error",
                        "task_id": task.id,
                        "message": str(llm_exc),
                    },
                ) from llm_exc
        return await complete_task(task, events, payload, "openai_responses", db, service)

    admin_dist = Path(__file__).resolve().parent.parent / "admin" / "dist"
    if admin_dist.is_dir():
        app.mount("/", StaticFiles(directory=admin_dist, html=True), name="admin")
    return app


app = create_app()

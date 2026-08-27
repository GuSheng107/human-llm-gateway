import json
import time
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
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from wechatpy.exceptions import InvalidSignatureException

from .config import Settings, get_settings
from .connectors import ConnectorManager
from .connectors.base import InboundMessage
from .db import get_db
from .dsl import ParsedEvent
from .enums import ConnectorPlatform, ConnectorStatus, EventKind, ReplySource, RouteMode, TaskStatus
from .model_catalog import list_public_models
from .models import (AdminUser, ApiKey, AuditLog, HumanOperator, IMConnection, LLMModel,
                     LLMProvider, ModelRoute, RequestEvent, RequestTask)
from .schemas import (ApiKeyCreate, ApiKeyCreated, ApiKeySummary, ConnectionCreate,
                      ConnectionSummary, HumanReplyRequest, LoginRequest, LoginResponse,
                      ProviderCreate, ProviderSummary, RouteCreate, RouteSummary, TaskSummary)
from .security import encrypt_secret, generate_api_key, issue_admin_token, verify_admin_token, verify_password
from .services import TaskError, TaskService, find_api_key, registry, seed_admin, task_to_dict
from .streaming import PseudoStreamer


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


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 settings: Settings = Depends(get_settings)) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员登录已失效")
    username = verify_admin_token(credentials.credentials, settings.app_secret)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员登录已失效")
    return username


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
        from .db import init_db, SessionLocal

        init_db()
        manager = ConnectorManager()
        application.state.connector_manager = manager

        async def handle_inbound(message: Any) -> None:
            from .db import SessionLocal

            with SessionLocal() as inbound_db:
                connection = inbound_db.get(IMConnection, message.connector_id)
                if connection is None:
                    return
                try:
                    config = json.loads(connection.config_json or "{}")
                except json.JSONDecodeError:
                    config = {}
                allowed = {str(item) for item in config.get("allowed_sender_ids", [])}
                if allowed and message.sender_id not in allowed:
                    inbound_db.add(AuditLog(action="connector.inbound_ignored",
                                            subject_type="im_connection",
                                            subject_id=str(message.connector_id), actor="connector",
                                            detail_json=json.dumps(
                                                {"sender_id": message.sender_id}, ensure_ascii=False)))
                    inbound_db.commit()
                    return
                query = (select(RequestTask).join(ApiKey).where(
                    ApiKey.im_connection_id == message.connector_id,
                    RequestTask.status.in_([TaskStatus.HUMAN_WAITING, TaskStatus.TOOL_PENDING]))
                    .order_by(RequestTask.created_at.desc()))
                task = inbound_db.execute(query).scalars().first()
                if task is None:
                    inbound_db.add(AuditLog(action="connector.inbound_ignored",
                                            subject_type="im_connection",
                                            subject_id=str(message.connector_id), actor="connector",
                                            detail_json=json.dumps({"reason": "no_waiting_task"},
                                                                    ensure_ascii=False)))
                    inbound_db.commit()
                    return
                try:
                    TaskService(get_settings(), manager).accept_reply(
                        inbound_db, task.id, message.text, ReplySource.IM,
                        f"connector:{message.connector_id}:{message.sender_id}",
                        message.external_message_id or None)
                except TaskError:
                    return

        manager.set_on_message(handle_inbound)
        with SessionLocal() as db:
            settings = get_settings()
            seed_admin(db, settings.admin_username, settings.admin_password)
        with SessionLocal() as startup_db:
            connections = list(startup_db.execute(select(IMConnection)).scalars())
        await manager.start_all(connections)
        yield
        await manager.stop_all()

    app = FastAPI(title="Human LLM Gateway", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": get_settings().app_name}

    @app.post("/admin/login", response_model=LoginResponse)
    def login(payload: LoginRequest, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)) -> LoginResponse:
        user = db.execute(select(AdminUser).where(AdminUser.username == payload.username,
                                                   AdminUser.active.is_(True))).scalar_one_or_none()
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return LoginResponse(access_token=issue_admin_token(user.username, settings.app_secret))

    @app.get("/v1/models")
    def models(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
        return {"object": "list", "data": list_public_models()}

    @app.post("/admin/api-keys", response_model=ApiKeyCreated)
    def create_key(payload: ApiKeyCreate, db: Session = Depends(get_db),
                   admin: str = Depends(require_admin)) -> ApiKeyCreated:
        secret, prefix, secret_hash = generate_api_key()
        operator = db.get(HumanOperator, payload.human_operator_id) if payload.human_operator_id else None
        if operator is None:
            operator = HumanOperator(display_name=payload.operator_name, status="offline")
            db.add(operator)
            db.flush()
        connection = db.get(IMConnection, payload.im_connection_id) if payload.im_connection_id else None
        if connection is None:
            connection = IMConnection(name=payload.im_name, platform=payload.platform,
                                      config_json=json.dumps(payload.im_config, ensure_ascii=False))
            db.add(connection)
            db.flush()
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

    @app.post("/admin/connectors", response_model=ConnectionSummary)
    def create_connection(payload: ConnectionCreate, db: Session = Depends(get_db),
                          admin: str = Depends(require_admin)) -> ConnectionSummary:
        connection = IMConnection(name=payload.name, platform=payload.platform,
                                  config_json=json.dumps(payload.config, ensure_ascii=False))
        db.add(connection)
        db.flush()
        db.add(AuditLog(action="connector.created", subject_type="im_connection",
                        subject_id=str(connection.id), actor=admin, detail_json=json.dumps(
                            {"platform": payload.platform.value}, ensure_ascii=False)))
        db.commit()
        return ConnectionSummary(id=connection.id, name=connection.name,
                                 platform=connection.platform, status=connection.status)

    @app.get("/admin/connectors", response_model=list[ConnectionSummary])
    def list_connections(_: str = Depends(require_admin), db: Session = Depends(get_db)) -> list[ConnectionSummary]:
        return [ConnectionSummary(id=c.id, name=c.name, platform=c.platform, status=c.status)
                for c in db.execute(select(IMConnection).order_by(IMConnection.id)).scalars()]

    @app.post("/admin/connectors/{connector_id}/start")
    async def start_connector(connector_id: int, _: str = Depends(require_admin),
                              db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="连接不存在")
        manager = app.state.connector_manager
        await manager.configure(connection)
        return await manager.health(connector_id)

    @app.post("/admin/connectors/{connector_id}/stop")
    async def stop_connector(connector_id: int, _: str = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="连接不存在")
        await app.state.connector_manager.stop_connection(connector_id)
        connection.status = ConnectorStatus.OFFLINE
        db.commit()
        return {"stopped": True}

    @app.get("/admin/connectors/{connector_id}/health")
    async def connector_health(connector_id: int, _: str = Depends(require_admin)) -> dict[str, Any]:
        return await app.state.connector_manager.health(connector_id)

    @app.post("/admin/connectors/{connector_id}/login")
    async def connector_login(connector_id: int, _: str = Depends(require_admin),
                              db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="连接不存在")
        manager = app.state.connector_manager
        if manager.get(connector_id) is None:
            await manager.configure(connection)
        try:
            return await manager.login(connector_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/admin/connectors/{connector_id}/login")
    async def connector_login_state(connector_id: int, _: str = Depends(require_admin),
                                    db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="连接不存在")
        manager = app.state.connector_manager
        connector = manager.get(connector_id)
        if connector is None:
            await manager.configure(connection)
            connector = manager.get(connector_id)
        if not hasattr(connector, "login_snapshot"):
            raise HTTPException(status_code=400, detail="该平台无登录流程")
        return connector.login_snapshot()

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

    @app.post("/internal/connectors/{connector_id}/messages")
    def inbound_message(connector_id: int, payload: HumanReplyRequest, task_id: str,
                        db: Session = Depends(get_db), service: TaskService = Depends(get_task_service)) -> dict[str, Any]:
        task = db.get(RequestTask, task_id)
        if not task or task.api_key.im_connection_id != connector_id:
            raise HTTPException(status_code=404, detail="任务或连接不存在")
        connection = db.get(IMConnection, connector_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="连接不存在")
        try:
            config = json.loads(connection.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        allowed = {str(item) for item in config.get("allowed_sender_ids", [])}
        if connection.platform is not ConnectorPlatform.FAKE and not payload.sender_id:
            raise HTTPException(status_code=400, detail="IM 回调缺少 sender_id")
        if allowed and str(payload.sender_id) not in allowed:
            db.add(AuditLog(action="task.reply_rejected_sender", subject_type="request_task",
                            subject_id=task.id, actor=f"connector:{connector_id}",
                            detail_json=json.dumps({"sender_id": payload.sender_id}, ensure_ascii=False)))
            db.commit()
            raise HTTPException(status_code=403, detail="发送者未绑定此 IM 连接")
        try:
            events = service.accept_reply(db, task_id, payload.text, ReplySource.IM,
                                          f"connector:{connector_id}:{payload.sender_id or 'fake'}",
                                          payload.external_message_id)
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id, "accepted": True, "events": [e.kind.value for e in events]}

    def openai_json(task: RequestTask, events: list[ParsedEvent]) -> dict[str, Any]:
        reasoning = "".join(e.content for e in events if e.kind is EventKind.REASONING)
        final = "".join(e.content for e in events if e.kind is EventKind.FINAL)
        tools = [{"id": f"call_{task.id[:8]}", "type": "function",
                  "function": {"name": e.tool_name, "arguments": e.tool_args_json}}
                 for e in events if e.kind is EventKind.TOOL_CALL]
        message: dict[str, Any] = {"role": "assistant", "content": final}
        if reasoning:
            message["reasoning_content"] = reasoning
        if tools:
            message["tool_calls"] = tools
        return {"id": f"chatcmpl-{task.id}", "object": "chat.completion", "created": int(time.time()),
                "model": task.model, "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": len(final), "total_tokens": len(final),
                           "estimated": True}}

    async def openai_stream(task: RequestTask, events: list[ParsedEvent], settings: Settings,
                            db: Session, service: TaskService) -> AsyncIterator[str]:
        streamer = PseudoStreamer(settings.stream_chunk_size, settings.stream_delay_min_ms,
                                  settings.stream_delay_max_ms)
        yield f": task_id={task.id}\n\n"
        index = 0
        async for event in streamer.events(events):
            if event.kind is EventKind.REASONING:
                delta = {"reasoning_content": event.content}
            elif event.kind is EventKind.TOOL_CALL:
                delta = {"tool_calls": [{"index": index, "id": f"call_{task.id[:8]}", "type": "function",
                                           "function": {"name": event.tool_name,
                                                        "arguments": event.tool_args_json}}]}
                index += 1
            else:
                delta = {"content": event.content}
            chunk = {"id": f"chatcmpl-{task.id}", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": task.model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        end = {"id": f"chatcmpl-{task.id}", "object": "chat.completion.chunk",
               "created": int(time.time()), "model": task.model,
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n"
        service.mark_completed(db, task.id)

    def anthropic_json(task: RequestTask, events: list[ParsedEvent]) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for event in events:
            if event.kind is EventKind.REASONING:
                content.append({"type": "thinking", "thinking": event.content})
            elif event.kind is EventKind.TOOL_CALL:
                try:
                    tool_input = json.loads(event.tool_args_json or "{}")
                except json.JSONDecodeError:
                    tool_input = {}
                content.append({"type": "tool_use", "id": f"toolu_{task.id[:8]}",
                                "name": event.tool_name, "input": tool_input})
            else:
                content.append({"type": "text", "text": event.content})
        return {"id": f"msg_{task.id}", "type": "message", "role": "assistant",
                "model": task.model, "content": content, "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0,
                           "output_tokens": sum(len(e.content) for e in events)}}

    async def anthropic_stream(task: RequestTask, events: list[ParsedEvent], settings: Settings,
                               db: Session, service: TaskService) -> AsyncIterator[str]:
        yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': anthropic_json(task, [])}, ensure_ascii=False)}\n\n"
        block_index = 0
        async for event in PseudoStreamer(settings.stream_chunk_size, settings.stream_delay_min_ms,
                                          settings.stream_delay_max_ms).events(events):
            if event.kind is EventKind.REASONING:
                if block_index == 0:
                    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'thinking', 'thinking': ''}}, ensure_ascii=False)}\n\n"
                delta_type, field = "thinking_delta", "thinking"
            elif event.kind is EventKind.TOOL_CALL:
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'tool_use', 'id': f'toolu_{task.id[:8]}', 'name': event.tool_name, 'input': {}}}, ensure_ascii=False)}\n\n"
                delta_type, field = "input_json_delta", "partial_json"
            else:
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'text', 'text': ''}}, ensure_ascii=False)}\n\n"
                delta_type, field = "text_delta", "text"
            value = event.content if event.kind is not EventKind.TOOL_CALL else (event.tool_args_json or "{}")
            delta = {"type": delta_type, field: value}
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_index, 'delta': delta}, ensure_ascii=False)}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_index})}\n\n"
            block_index += 1
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
        yield "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
        service.mark_completed(db, task.id)

    async def complete_task(task: RequestTask, events: list[ParsedEvent], payload: dict[str, Any],
                            protocol: str, db: Session, service: TaskService) -> Any:
        if payload.get("stream"):
            if protocol == "anthropic":
                return StreamingResponse(anthropic_stream(task, events, get_settings(), db, service),
                                          media_type="text/event-stream")
            return StreamingResponse(openai_stream(task, events, get_settings(), db, service),
                                     media_type="text/event-stream")
        service.mark_completed(db, task.id)
        return JSONResponse(anthropic_json(task, events) if protocol == "anthropic"
                            else openai_json(task, events))

    @app.post("/connectors/webhook/{connector_id}/inbound")
    async def webhook_inbound(connector_id: int, request: Request,
                              token: str = Header(default="", alias="X-Connector-Token"),
                              db: Session = Depends(get_db)) -> dict[str, Any]:
        connection = db.get(IMConnection, connector_id)
        if connection is None or connection.platform is not ConnectorPlatform.WEBHOOK:
            raise HTTPException(status_code=404, detail="连接不存在")
        try:
            config = json.loads(connection.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        if not token or token != str(config.get("inbound_token", "")):
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
        )
        await app.state.connector_manager.dispatch(message)
        return {"accepted": True}

    @app.api_route("/connectors/wecom/{connector_id}/callback", methods=["GET", "POST"])
    async def wecom_callback(connector_id: int, request: Request,
                             msg_signature: str = Query(...), timestamp: str = Query(...),
                             nonce: str = Query(...), echostr: str = Query(default=""),
                             db: Session = Depends(get_db)) -> Any:
        connection = db.get(IMConnection, connector_id)
        if connection is None or connection.platform is not ConnectorPlatform.WECOM:
            raise HTTPException(status_code=404, detail="连接不存在")
        manager = app.state.connector_manager
        connector = manager.get(connector_id)
        if connector is None:
            await manager.configure(connection)
            connector = manager.get(connector_id)
        if connector is None:
            raise HTTPException(status_code=503, detail="连接器未就绪")
        if request.method == "GET":
            if not echostr:
                raise HTTPException(status_code=400, detail="URL 验证需要 echostr")
            try:
                return PlainTextResponse(connector.verify_url(msg_signature, timestamp, nonce, echostr))
            except InvalidSignatureException:
                raise HTTPException(status_code=403, detail="签名校验失败")
        body = (await request.body()).decode("utf-8")
        try:
            message = connector.parse_inbound(body, msg_signature, timestamp, nonce)
        except InvalidSignatureException:
            raise HTTPException(status_code=403, detail="消息解密失败")
        if message is not None:
            await manager.dispatch(message)
        return PlainTextResponse("success")

    @app.websocket("/connectors/ws/{connector_id}")
    async def ws_connect(websocket: WebSocket, connector_id: int, token: str = Query(default="")) -> None:
        from .db import SessionLocal

        with SessionLocal() as db:
            connection = db.get(IMConnection, connector_id)
            valid = (connection is not None
                     and connection.platform is ConnectorPlatform.WEBSOCKET)
            expected_token = ""
            if valid:
                try:
                    config = json.loads(connection.config_json or "{}")
                    expected_token = str(config.get("auth_token", ""))
                except json.JSONDecodeError:
                    valid = False
        if not valid or not expected_token or token != expected_token:
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

    admin_dist = Path(__file__).resolve().parent.parent / "admin" / "dist"
    if admin_dist.is_dir():
        app.mount("/", StaticFiles(directory=admin_dist, html=True), name="admin")
    return app


app = create_app()

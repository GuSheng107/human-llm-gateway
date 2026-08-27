import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import Settings
from .connectors import ConnectorManager, OutboundTask
from .dsl import DSLParseError, ParsedEvent, parse_human_reply
from .enums import EventKind, ReplySource, RouteMode, TaskStatus
from .llm import LLMAdapter, LLMError
from .models import ApiKey, AuditLog, ModelRoute, RequestEvent, RequestTask
from .security import hash_password, verify_api_key


class TaskError(RuntimeError):
    pass


class TaskRegistry:
    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[list[ParsedEvent]]] = {}

    def register(self, task_id: str) -> asyncio.Future[list[ParsedEvent]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[ParsedEvent]] = loop.create_future()
        self._waiters[task_id] = future
        return future

    def resolve(self, task_id: str, events: list[ParsedEvent]) -> None:
        future = self._waiters.get(task_id)
        if future and not future.done():
            # A webhook may be handled by another worker thread in tests or by
            # an adapter callback.  Always schedule completion on the waiter's
            # event loop instead of touching its Future from the caller thread.
            future.get_loop().call_soon_threadsafe(future.set_result, events)

    def remove(self, task_id: str) -> None:
        self._waiters.pop(task_id, None)


registry = TaskRegistry()


def task_to_dict(task: RequestTask) -> dict[str, Any]:
    return {"id": task.id, "api_key_id": task.api_key_id, "protocol": task.protocol,
            "model": task.model, "status": task.status.value, "error": task.error,
            "created_at": task.created_at.isoformat()}


class TaskService:
    def __init__(self, settings: Settings, connector_manager: ConnectorManager | None = None) -> None:
        self.settings = settings
        self.llm = LLMAdapter()
        self.connector_manager = connector_manager or ConnectorManager()

    def create_human_task(self, db: Session, key: ApiKey, protocol: str, model: str,
                          request: dict[str, Any]) -> RequestTask:
        route = key.route
        task = RequestTask(api_key_id=key.id, protocol=protocol, model=route.model_name,
                           request_json=json.dumps(request, ensure_ascii=False),
                           status=TaskStatus.HUMAN_WAITING)
        db.add(task)
        db.flush()
        db.add(AuditLog(action="task.created", subject_type="request_task", subject_id=task.id,
                        actor=key.prefix, detail_json=json.dumps({"mode": route.mode.value})))
        db.commit()
        task = db.get(RequestTask, task.id)
        assert task is not None
        prompt = self._human_prompt(task, request)
        target = self._connection_target(key)
        # Delivery is deliberately asynchronous: the API request must be
        # allowed to wait while the connector handles a slow IM network.
        asyncio.create_task(self._deliver_human(key, OutboundTask(
            task_id=task.id, text=prompt, target=target)))
        return task

    @staticmethod
    def _human_prompt(task: RequestTask, request: dict[str, Any]) -> str:
        messages = request.get("messages") or []
        last = messages[-1] if messages else {}
        content = last.get("content", "") if isinstance(last, dict) else ""
        if isinstance(content, list):
            content = " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return f"任务 {task.id}\n\n用户消息：\n{content}\n\n请回复完整 DSL（/think /tool /reply /done）。"

    @staticmethod
    def _connection_target(key: ApiKey) -> str:
        try:
            config = json.loads(key.im_connection.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        return str(config.get("chat_id", config.get("conversation_id", "")))

    async def _deliver_human(self, key: ApiKey, task: OutboundTask) -> None:
        try:
            await self.connector_manager.send_task(key.im_connection, task)
        except Exception:
            # The task remains human_waiting and can still be answered from
            # the web console.  The caller never receives an upstream 500 just
            # because a connector delivery failed.
            return

    def create_llm_task(self, db: Session, key: ApiKey, protocol: str, model: str,
                        request: dict[str, Any]) -> RequestTask:
        task = RequestTask(api_key_id=key.id, protocol=protocol, model=key.route.model_name,
                           request_json=json.dumps(request, ensure_ascii=False),
                           status=TaskStatus.LLM_STREAMING)
        db.add(task)
        db.flush()
        db.commit()
        return task

    async def complete_llm_task(self, db: Session, task_id: str,
                                messages: list[dict[str, Any]]) -> list[ParsedEvent]:
        task = db.execute(select(RequestTask).where(RequestTask.id == task_id)
                          .options(joinedload(RequestTask.api_key).joinedload(ApiKey.route)
                                   .joinedload(ModelRoute.provider))).scalar_one_or_none()
        if task is None:
            raise TaskError("任务不存在")
        provider = task.api_key.route.provider
        if provider is None:
            self.fail_task(db, task.id, "LLM 路由未配置供应商")
            raise TaskError("LLM 路由未配置供应商")
        task.status = TaskStatus.LLM_STREAMING
        db.commit()
        try:
            upstream_model = task.api_key.route.upstream_model or task.api_key.route.model_name
            events = await self.llm.complete(provider, upstream_model, messages, self.settings.app_secret)
        except LLMError as exc:
            self.fail_task(db, task.id, str(exc))
            raise TaskError(str(exc)) from exc
        for index, event in enumerate(events, start=1):
            db.add(RequestEvent(task_id=task.id, sequence=index, kind=event.kind,
                                content=event.content, tool_name=event.tool_name,
                                tool_args_json=event.tool_args_json, source=ReplySource.LLM))
        task.status = TaskStatus.PSEUDO_STREAMING
        db.add(AuditLog(action="task.llm_completed", subject_type="request_task",
                        subject_id=task.id, actor="llm",
                        detail_json=json.dumps({"provider_id": provider.id}, ensure_ascii=False)))
        db.commit()
        return events

    async def await_human(self, task_id: str, timeout: float) -> list[ParsedEvent]:
        future = registry._waiters.get(task_id) or registry.register(task_id)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TaskError("人工回复超时") from exc
        finally:
            registry.remove(task_id)

    def accept_reply(self, db: Session, task_id: str, text: str, source: ReplySource,
                     actor: str, external_message_id: str | None = None) -> list[ParsedEvent]:
        task = db.execute(select(RequestTask).where(RequestTask.id == task_id)
                          .options(joinedload(RequestTask.api_key))).scalar_one_or_none()
        if task is None:
            raise TaskError("任务不存在")
        if external_message_id:
            duplicate = db.execute(select(RequestEvent).where(
                RequestEvent.task_id == task_id,
                RequestEvent.external_message_id == external_message_id)).scalar_one_or_none()
            if duplicate:
                return [ParsedEvent(e.kind, e.content, e.tool_name, e.tool_args_json)
                        for e in task.events]
        if task.status not in {TaskStatus.HUMAN_WAITING, TaskStatus.TOOL_PENDING}:
            raise TaskError("任务已结束，不能重复回复")
        try:
            events = parse_human_reply(text, self.settings.allow_plain_human_reply)
        except DSLParseError as exc:
            task.error = str(exc)
            task.status = TaskStatus.FAILED
            db.add(AuditLog(action="task.reply_rejected", subject_type="request_task",
                            subject_id=task.id, actor=actor,
                            detail_json=json.dumps({"source": source.value}, ensure_ascii=False)))
            db.commit()
            raise TaskError(str(exc)) from exc
        for index, event in enumerate(events, start=1):
            db.add(RequestEvent(task_id=task.id, sequence=index, kind=event.kind,
                                content=event.content, tool_name=event.tool_name,
                                tool_args_json=event.tool_args_json, source=source,
                                external_message_id=external_message_id if index == 1 else None))
        task.status = TaskStatus.PSEUDO_STREAMING
        db.add(AuditLog(action="task.reply_accepted", subject_type="request_task",
                        subject_id=task.id, actor=actor,
                        detail_json=json.dumps({"source": source.value}, ensure_ascii=False)))
        db.commit()
        registry.resolve(task.id, events)
        return events

    def mark_completed(self, db: Session, task_id: str) -> None:
        task = db.get(RequestTask, task_id)
        if task and task.status is TaskStatus.PSEUDO_STREAMING:
            task.status = TaskStatus.COMPLETED
            db.commit()

    def mark_timeout(self, db: Session, task_id: str) -> None:
        task = db.get(RequestTask, task_id)
        if task and task.status is TaskStatus.HUMAN_WAITING:
            task.status = TaskStatus.TIMEOUT
            db.commit()

    def fail_task(self, db: Session, task_id: str, error: str) -> None:
        task = db.get(RequestTask, task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error = error
            db.commit()


def seed_admin(db: Session, username: str, password: str) -> None:
    from .models import AdminUser

    if db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none() is None:
        db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()


def find_api_key(db: Session, secret: str) -> ApiKey | None:
    candidates = db.execute(select(ApiKey).where(ApiKey.active.is_(True))).scalars().all()
    for candidate in candidates:
        if verify_api_key(secret, candidate.secret_hash):
            return db.execute(select(ApiKey).where(ApiKey.id == candidate.id)
                              .options(joinedload(ApiKey.human_operator),
                                       joinedload(ApiKey.im_connection),
                                       joinedload(ApiKey.route).joinedload(ModelRoute.provider))).scalar_one()
    return None

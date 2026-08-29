"""API Key 用例：创建（明文只展示一次）、策略配置与生命周期。

Key 决定请求归属、回复入口、回复策略和可用模型集合；
停用或删除立即阻止新请求，已准入任务按创建快照继续完成。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.constants import HUMAN_TIMEOUT_MAX_SECONDS, HUMAN_TIMEOUT_MIN_SECONDS
from ..core.db import begin_immediate_if_sqlite
from ..core.security import generate_api_key
from ..domain.enums import (
    AuditAction,
    DeliveryMode,
    ReplyStrategy,
    UserRole,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.api_keys import ApiKeyRepository
from ..repositories.catalog import FakeModelRepository
from ..repositories.connections import ConnectionRepository
from ..repositories.models import ApiKey, User
from ..repositories.system import AuditRepository


class ApiKeyService:
    def __init__(self) -> None:
        self.repo = ApiKeyRepository()
        self.catalog = FakeModelRepository()
        self.connections = ConnectionRepository()
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_owned(self, session: Session, key_pk: int, user: User) -> ApiKey:
        row = self.repo.get(session, key_pk)
        if row is None or (user.role is not UserRole.ADMIN and row.owner_user_id != user.id):
            raise DomainError(DomainErrorCode.NOT_FOUND, "API Key 不存在", status_code=404)
        return row

    # ------------------------------------------------------------------
    # 创建与更新
    # ------------------------------------------------------------------

    def create(
        self,
        session: Session,
        *,
        owner: User,
        name: str,
        delivery_mode: DeliveryMode = DeliveryMode.WEB,
        im_connection_id: int | None = None,
        reply_strategy: ReplyStrategy = ReplyStrategy.HUMAN,
        llm_config_id: int | None = None,
        human_timeout_seconds: int = 300,
        model_group_id: int | None = None,
        fake_model_ids: list[int] | None = None,
    ) -> tuple[ApiKey, str]:
        """创建 Key；返回 (行, 明文)。明文只在创建响应展示一次。"""
        begin_immediate_if_sqlite(session)
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "Key 名称不能为空且最多 100 字符",
                status_code=400,
            )
        normalized = self._validate_delivery_and_strategy(
            session,
            owner=owner,
            delivery_mode=delivery_mode,
            im_connection_id=im_connection_id,
            reply_strategy=reply_strategy,
            llm_config_id=llm_config_id,
        )
        self._validate_timeout(human_timeout_seconds)
        group_id = self._validate_model_group(session, owner, model_group_id)
        row = ApiKey(
            owner_user_id=owner.id,
            name=name,
            **normalized,
            human_timeout_seconds=human_timeout_seconds,
            model_group_id=group_id,
        )
        plaintext, prefix, key_hash = generate_api_key()
        row.key_prefix = prefix
        row.key_hash = key_hash
        self.repo.add(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "同名 Key 已存在", status_code=409) from exc
        # 显式模型选择：验证候选集后原子替换。
        selected_ids = self._validate_selected_models(
            session,
            owner=owner,
            key_row=row,
            model_group_id=group_id,
            fake_model_ids=fake_model_ids,
        )
        if selected_ids:
            self.catalog.replace_key_models(session, row.id, selected_ids)
        self.audit.add(
            session,
            action=AuditAction.API_KEY_CREATED,
            resource_type="api_key",
            resource_id=str(row.id),
            actor_user_id=owner.id,
            owner_user_id=owner.id,
            metadata={"fields": ["name", "delivery_mode", "reply_strategy"]},
        )
        return row, plaintext

    def update(
        self, session: Session, *, row: ApiKey, actor: User, fields: dict[str, Any]
    ) -> ApiKey:
        """PATCH：只修改显式提交的字段。"""
        # 读-改-写（含 replace_key_models 删除+插入）需先取写锁，避免 SQLite 锁升级竞争。
        begin_immediate_if_sqlite(session)
        owner_id = row.owner_user_id
        changed: list[str] = []
        if "name" in fields:
            new_name = (fields["name"] or "").strip()
            if not new_name or len(new_name) > 100:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "Key 名称不能为空且最多 100 字符",
                    status_code=400,
                )
            if new_name != row.name:
                row.name = new_name
                changed.append("name")
        if (
            "enabled" in fields
            and fields["enabled"] is not None
            and bool(fields["enabled"]) != row.is_enabled
        ):
            row.is_enabled = bool(fields["enabled"])
            changed.append("is_enabled")
        delivery_mode = (
            DeliveryMode(fields["delivery_mode"]) if fields.get("delivery_mode") else None
        )
        reply_strategy = (
            ReplyStrategy(fields["reply_strategy"]) if fields.get("reply_strategy") else None
        )
        im_connection_id = fields.get("im_connection_id", row.im_connection_id)
        llm_config_id = fields.get("llm_config_id", row.llm_config_id)
        if delivery_mode is not None:
            row.delivery_mode = delivery_mode
            changed.append("delivery_mode")
        if reply_strategy is not None:
            row.reply_strategy = reply_strategy
            changed.append("reply_strategy")
        if "im_connection_id" in fields:
            row.im_connection_id = im_connection_id
            changed.append("im_connection_id")
        if "llm_config_id" in fields:
            row.llm_config_id = llm_config_id
            changed.append("llm_config_id")
        if (
            delivery_mode is not None
            or reply_strategy is not None
            or "im_connection_id" in fields
            or "llm_config_id" in fields
        ):
            self._validate_delivery_and_strategy(
                session,
                owner_row_id=owner_id,
                actor=actor,
                delivery_mode=row.delivery_mode,
                im_connection_id=row.im_connection_id,
                reply_strategy=row.reply_strategy,
                llm_config_id=row.llm_config_id,
            )
        if "human_timeout_seconds" in fields and fields["human_timeout_seconds"] is not None:
            self._validate_timeout(int(fields["human_timeout_seconds"]))
            if row.human_timeout_seconds != int(fields["human_timeout_seconds"]):
                row.human_timeout_seconds = int(fields["human_timeout_seconds"])
                changed.append("human_timeout_seconds")
        if "model_group_id" in fields:
            group_id = self._validate_model_group(
                session, _owner_stub(owner_id), fields["model_group_id"]
            )
            if row.model_group_id != group_id:
                row.model_group_id = group_id
                changed.append("model_group_id")
        if "fake_model_ids" in fields:
            selected_ids = self._validate_selected_models(
                session,
                owner=_owner_stub(owner_id),
                key_row=row,
                model_group_id=row.model_group_id,
                fake_model_ids=fields["fake_model_ids"],
                actor=actor,
            )
            # 空集合删除全部关联行，语义为允许全部候选模型。
            self.catalog.replace_key_models(session, row.id, selected_ids)
            changed.append("fake_model_ids")
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "同名 Key 已存在", status_code=409) from exc
        if changed:
            self.audit.add(
                session,
                action=AuditAction.API_KEY_UPDATED,
                resource_type="api_key",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                owner_user_id=owner_id,
                metadata={"fields": changed},
            )
        return row

    def delete(self, session: Session, *, row: ApiKey, actor: User) -> None:
        """立即阻止新请求并软删除；已准入任务按快照继续（FK 保留）。"""
        self.repo.soft_delete(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.API_KEY_DELETED,
            resource_type="api_key",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["deleted_at", "is_enabled"]},
        )

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def _validate_timeout(self, seconds: int) -> None:
        if not HUMAN_TIMEOUT_MIN_SECONDS <= seconds <= HUMAN_TIMEOUT_MAX_SECONDS:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"人工超时时间必须在 {HUMAN_TIMEOUT_MIN_SECONDS} 到 {HUMAN_TIMEOUT_MAX_SECONDS} 秒之间",
                status_code=400,
            )

    def _validate_delivery_and_strategy(
        self,
        session: Session,
        *,
        owner: User | None = None,
        owner_row_id: int | None = None,
        actor: User | None = None,
        delivery_mode: DeliveryMode,
        im_connection_id: int | None,
        reply_strategy: ReplyStrategy,
        llm_config_id: int | None,
    ) -> dict:
        """校验入口与策略组合，返回 Key 行的规范化字段。"""
        owner_id = owner.id if owner is not None else owner_row_id
        assert owner_id is not None
        fields: dict = {
            "delivery_mode": delivery_mode,
            "reply_strategy": reply_strategy,
            "im_connection_id": None,
            "llm_config_id": None,
        }
        if delivery_mode is DeliveryMode.IM:
            if im_connection_id is None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "IM 入口必须选择一个自己的连接",
                    status_code=400,
                )
            connection = self.connections.get(session, im_connection_id)
            if connection is None or connection.owner_user_id != owner_id:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "投递入口必须是自己的连接", status_code=400
                )
            fields["im_connection_id"] = im_connection_id
        else:
            if im_connection_id is not None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "Web 入口不能绑定连接", status_code=400
                )
        if reply_strategy in (ReplyStrategy.LLM, ReplyStrategy.HUMAN_FALLBACK_LLM):
            if llm_config_id is None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"策略 {reply_strategy.value} 必须选择 LLM 配置",
                    status_code=400,
                )
            from ..repositories.models import LlmConfig

            config = session.get(LlmConfig, llm_config_id)
            if config is None or config.deleted_at is not None or config.owner_user_id != owner_id:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "LLM 配置必须是自己的有效配置",
                    status_code=400,
                )
            fields["llm_config_id"] = llm_config_id
        elif llm_config_id is not None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "人工策略不需要 LLM 配置", status_code=400
            )
        return fields

    def _validate_model_group(
        self, session: Session, owner: User, model_group_id: int | None
    ) -> int | None:
        if model_group_id is None:
            return None
        group = self.catalog.get_group(session, model_group_id)
        if group is None or group.owner_user_id != owner.id:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "模型分组必须是自己的有效分组", status_code=400
            )
        return model_group_id

    def _validate_selected_models(
        self,
        session: Session,
        *,
        owner: User,
        key_row: ApiKey | None,
        model_group_id: int | None,
        fake_model_ids: list[int] | None,
        actor: User | None = None,
    ) -> list[int]:
        """Key 显式选择只能来自分组预筛后的候选集（§10.6）。"""
        if not fake_model_ids:
            return []
        requested = list(dict.fromkeys(int(mid) for mid in fake_model_ids))
        visible_ids = {model.id for model in self.catalog.visible_models(session, owner.id)}
        candidate_ids = visible_ids
        if model_group_id is not None:
            group = self.catalog.get_group(session, model_group_id)
            if group is not None:
                candidate_ids = {
                    model.id for model in self.catalog.group_items(session, group.id)
                } & visible_ids
        invalid = [mid for mid in requested if mid not in candidate_ids]
        if invalid:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "所选模型必须在分组预筛后的候选集内",
                status_code=400,
            )
        return requested


def _owner_stub(user_id: int) -> User:
    """更新路径只需要 id 构造可见集合查询。"""
    return User(id=user_id, username="", display_name="", password_hash="", role=UserRole.USER)

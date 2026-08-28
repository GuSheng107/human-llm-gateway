"""面向兼容 API 客户端公开的模型目录。

`PUBLIC_TEXT_MODELS` 只是全新数据库初始化时的默认种子常量，不是运行时真源。
运行时公开模型持久化在 `public_models` 表，由管理员通过 /admin/models 接口维护；
`GET /v1/models` 只读取数据库。种子通过 SystemSetting 标记保证幂等：管理员删除
或停用全部模型后，服务重启不会再次补回默认模型。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PublicModel, SystemSetting

PUBLIC_TEXT_MODELS: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "openai": ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"),
    "claude": (
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ),
    "kimi": ("kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"),
    "minimax": ("MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed"),
    "grok": ("grok-4.6", "grok-4.5", "grok-4.3"),
    "qwen": (
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.7-flash",
    ),
    "glm": ("glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-4.7"),
    "gemini": (
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ),
}

SEED_FLAG_KEY = "public_models_seeded"


def default_public_models() -> list[dict[str, str]]:
    """返回默认种子内容（纯常量展开，不触库）。"""
    return [
        {"id": model_id, "object": "model", "owned_by": vendor}
        for vendor, model_ids in PUBLIC_TEXT_MODELS.items()
        for model_id in model_ids
    ]


def list_public_models(db: Session, *, include_inactive: bool = False) -> list[PublicModel]:
    """从数据库读取运行时公开模型目录，按 sort_order、model_id 排序。"""
    stmt = select(PublicModel).order_by(PublicModel.sort_order, PublicModel.model_id)
    if not include_inactive:
        stmt = stmt.where(PublicModel.active.is_(True))
    return list(db.execute(stmt).scalars())


def seed_public_models(db: Session) -> int:
    """为全新数据库写入默认公开模型，返回实际写入条数。

    幂等：首次执行后以 SystemSetting 标记完成；此后即使表被管理员清空，
    重启也不会再次补种。
    """
    if db.get(SystemSetting, SEED_FLAG_KEY) is not None:
        return 0
    existing = set(db.execute(select(PublicModel.model_id)).scalars())
    order = 0
    inserted = 0
    for item in default_public_models():
        if item["id"] in existing:
            continue
        db.add(
            PublicModel(
                model_id=item["id"],
                owned_by=item["owned_by"],
                sort_order=order,
                active=True,
            )
        )
        inserted += 1
        order += 1
    db.add(SystemSetting(key=SEED_FLAG_KEY, value="1"))
    db.commit()
    return inserted

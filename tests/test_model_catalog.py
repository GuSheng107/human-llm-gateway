from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.model_catalog import (
    PUBLIC_TEXT_MODELS,
    default_public_models,
    list_public_models,
    seed_public_models,
)
from app.models import Base, PublicModel


def make_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_default_model_catalog_is_complete_and_unique():
    assert list(PUBLIC_TEXT_MODELS) == [
        "deepseek",
        "openai",
        "claude",
        "kimi",
        "minimax",
        "grok",
        "qwen",
        "glm",
        "gemini",
    ]
    ids = [item["id"] for item in default_public_models()]
    assert len(ids) == 34
    assert len(ids) == len(set(ids))
    assert ids[:2] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert "gpt-5.6-sol" in ids
    assert "claude-fable-5" in ids
    assert "kimi-k2.7-code-highspeed" in ids
    assert "MiniMax-M3" in ids
    assert "grok-4.6" in ids
    assert "qwen3.8-max" in ids
    assert "glm-5.3" in ids
    assert ids[-1] == "gemini-3.5-flash-lite"


def test_seed_public_models_writes_defaults_once():
    with make_session() as db:
        assert seed_public_models(db) == 34
        # 幂等：第二次调用不再写入
        assert seed_public_models(db) == 0
        items = list_public_models(db)
        assert len(items) == 34
        assert items[0].model_id == "deepseek-v4-pro"
        assert items[-1].model_id == "gemini-3.5-flash-lite"


def test_seed_does_not_repopulate_after_admin_clears_catalog():
    with make_session() as db:
        seed_public_models(db)
        for item in list(db.execute(select(PublicModel)).scalars()):
            db.delete(item)
        db.commit()
        # 模拟服务重启：种子标记已存在，不得再次补回默认模型
        assert seed_public_models(db) == 0
        assert list_public_models(db) == []


def test_list_public_models_filters_inactive_and_sorts():
    with make_session() as db:
        seed_public_models(db)
        first = db.execute(
            select(PublicModel).where(PublicModel.model_id == "deepseek-v4-pro")
        ).scalar_one()
        first.active = False
        zzz = db.execute(
            select(PublicModel).where(PublicModel.model_id == "deepseek-v4-flash")
        ).scalar_one()
        zzz.sort_order = 999
        db.commit()
        items = list_public_models(db)
        ids = [item.model_id for item in items]
        assert "deepseek-v4-pro" not in ids
        assert ids[-1] == "deepseek-v4-flash"
        assert len(list_public_models(db, include_inactive=True)) == 34

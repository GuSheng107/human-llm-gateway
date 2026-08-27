from app.model_catalog import PUBLIC_TEXT_MODELS, list_public_models


def test_public_model_catalog_is_complete_and_unique():
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
    models = list_public_models()
    ids = [item["id"] for item in models]
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

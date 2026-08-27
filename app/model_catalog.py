"""面向兼容 API 客户端公开的文本模型目录。"""

PUBLIC_TEXT_MODELS: dict[str, tuple[str, ...]] = {
    "deepseek": (
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ),
    "openai": (
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
    ),
    "claude": (
        "claude-fable-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    ),
    "kimi": (
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
    ),
    "minimax": (
        "MiniMax-M3",
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ),
    "grok": (
        "grok-4.6",
        "grok-4.5",
        "grok-4.3",
    ),
    "qwen": (
        "qwen3.8-max",
        "qwen3.8-flash",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.7-flash",
    ),
    "glm": (
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.2",
        "glm-4.7",
    ),
    "gemini": (
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ),
}


def list_public_models() -> list[dict[str, str]]:
    return [
        {"id": model_id, "object": "model", "owned_by": vendor}
        for vendor, model_ids in PUBLIC_TEXT_MODELS.items()
        for model_id in model_ids
    ]

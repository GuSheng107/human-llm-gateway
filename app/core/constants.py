"""跨模块共享的稳定常量。

不属于任何单一领域、也不依赖环境的固定值放在这里；环境相关值走 config。
"""

# 数据库 Schema 版本：与代码不一致时启动失败，不执行迁移。
SCHEMA_VERSION = 1

# 加密契约（详见 docs/DATABASE.md §2.4）
SECRET_ENVELOPE_PREFIX = "hlg1"
SECRET_KEY_VERSION = 1
SECRET_HKDF_INFO = b"human-llm-gateway/secret-encryption/v1"

# 请求体大小上限（详见 docs/API_CONTRACT.md §2.1 / §16.3）
MAX_INFERENCE_REQUEST_BYTES = 8 * 1024 * 1024  # /v1/* 推理请求
MAX_ADMIN_REQUEST_BYTES = 1 * 1024 * 1024  # /api/* 管理 JSON

# 用户级活动任务上限
MAX_ACTIVE_TASKS_PER_USER = 10

# 人工等待超时范围（秒）
HUMAN_TIMEOUT_MIN_SECONDS = 10
HUMAN_TIMEOUT_MAX_SECONDS = 1800
HUMAN_TIMEOUT_DEFAULT_SECONDS = 300

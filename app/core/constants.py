"""跨模块共享的稳定常量。

不属于任何单一领域、也不依赖环境的固定值放在这里；环境相关值走 config。
"""

# 数据库 Schema 版本：与代码不一致时启动失败，不执行迁移。
SCHEMA_VERSION = 4

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

# IM 连接自动重连（带抖动指数退避，见 docs/DATABASE.md §4.1）
CONNECTION_BACKOFF_BASE_SECONDS = 2
CONNECTION_BACKOFF_MAX_SECONDS = 300
CONNECTION_BACKOFF_JITTER_RATIO = 0.2
CONNECTION_HEALTHY_RESET_SECONDS = 60

# 绑定码默认有效期（秒）；config.binding_code_ttl_seconds 可覆盖
BINDING_CODE_TTL_FALLBACK_SECONDS = 300

# 历史响应链展开三重上限（docs/API_CONTRACT.md §12.5）
MAX_CONTEXT_CHAIN_DEPTH = 20
MAX_EXPANDED_ITEMS = 512
MAX_EXPANDED_CONTEXT_BYTES = 2 * 1024 * 1024

# LLM 配置相关（docs/API_CONTRACT.md §6）
LLM_TIMEOUT_MIN_SECONDS = 5
LLM_TIMEOUT_MAX_SECONDS = 600
LLM_TIMEOUT_DEFAULT_SECONDS = 120
LLM_NAME_MAX_LENGTH = 100
LLM_BASE_URL_MAX_LENGTH = 2048
LLM_MODEL_MAX_LENGTH = 255
# 连通性测试硬上限（无论配置 timeout_seconds 多大）
LLM_CONNECT_TEST_TIMEOUT_SECONDS = 10
# Anthropic 上游请求 max_tokens 缺省值（请求未带输出上限时）
LLM_DEFAULT_MAX_TOKENS = 1024

# 上游响应体积/时长上限（SSRF 配套的资源防护）
# 非流式响应体上限（httpx aread 后解析前检查）。
LLM_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# 流式：累计字节上限与总接收时长上限（httpx timeout 只约束单次读写）。
LLM_MAX_STREAM_BYTES = 16 * 1024 * 1024
LLM_MAX_STREAM_SECONDS = 600.0
# SSE 单行上限（防御无换行的恶意长行占满内存）。
LLM_MAX_SSE_LINE_BYTES = 1024 * 1024

# SSRF 无条件拒绝：云元数据主机与网段（合法自建场景无理由访问）。
SSRF_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "100.100.100.200",  # 阿里云元数据
        "168.63.129.16",  # Azure WireServer
        "metadata.google.internal",
    }
)
SSRF_METADATA_NETWORKS = ("169.254.0.0/16",)

# 工具沙箱（M12）：进程级隔离限制。
TOOL_MIN_TIMEOUT_SECONDS = 1
TOOL_MAX_TIMEOUT_SECONDS = 120
TOOL_MAX_STDOUT_BYTES = 64 * 1024  # 单边输出上限（截断保存）
TOOL_MAX_NAME_LENGTH = 100
TOOL_MAX_COMMAND_LENGTH = 2000
TOOL_MAX_ARGUMENTS = 16
TOOL_MAX_ARGUMENT_VALUE_LENGTH = 4096

# 外部 API Key：`sk-` + 32 字节随机数的无 padding base64url（43 字符）。
API_KEY_PREFIX = "sk-"
API_KEY_PREFIX_LENGTH = 8
API_KEY_RANDOM_BYTES = 32

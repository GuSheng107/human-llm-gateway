# Human LLM Gateway 架构

## 1. 边界

系统是单进程 FastAPI 应用，包含协议适配、任务状态、IM 连接器、真实 LLM 适配和 SQLite 持久化。React 管理台构建后由同一进程托管。

```text
OpenAI / Anthropic client
          |
     protocol layer
          |
  API key + model route
          |
       task service -------- real LLM adapter
          |
   inbound processor
          |
 connector registry/manager
    |      |      |      |
 iLink  WeCom  Webhook  WS/HTTP
```

项目只支持当前模型直接创建的新数据库，不包含表结构迁移或旧数据兼容分支。

## 2. 分层

- `app/protocols/`：OpenAI Chat、OpenAI Responses、Anthropic Messages 的 JSON/SSE 渲染。
- `app/services.py`：任务创建、等待、人工回复解析、真实 LLM 回退和终态更新。
- `app/inbound.py`：所有 IM 消息的绑定校验、精确任务选择和全局幂等。
- `app/im_connections.py`：用户 Bot 生命周期和一次性绑定流程。
- `app/connectors/registry.py`：平台元数据、配置字段、能力和 Factory 注册。
- `app/connectors/manager.py`：连接实例生命周期、状态持久化和统一投递。
- `app/connection_config.py`：IM 凭据整体加密和严格解密。
- `app/dblog.py`：标准化运行日志写入 `app_logs`，存储失败时输出结构化 stderr。
- `app/models.py`：当前数据库的唯一结构定义。
- `app/model_catalog.py`：公开模型目录的默认种子常量与幂等种子函数；运行时真源是 `public_models` 表。

## 3. 身份与所有权

`AdminUser` 同时表示管理员和普通用户，角色由 `UserRole` 区分。每个 `IMConnection` 必须有非空 `owner_id`：

- 普通用户只能列出和管理自己的连接。
- 管理员可以列出、检查、启动、停止和删除全部连接。
- 管理员不能创建 Bot、生成绑定码或读取扫码登录状态。
- 一次性绑定码只保存哈希；成功后记录平台 userid 和 conversation id。
- 新回复必须来自连接当前绑定的 userid。

API Key 绑定一个 `HumanOperator`、一个用户 Bot 和一个 `ModelRoute`。管理员负责建立 API 路由，Bot 所有权仍属于普通用户。

## 4. 进站消息

所有平台归一化为 `InboundMessage`：

```text
connector_id
sender_id
conversation_id
external_message_id
reply_to_task_id
text
```

处理顺序固定为：连接存在检查、发送者存在检查、幂等占位、绑定命令、绑定身份校验、任务定位、DSL 解析与事件持久化。

没有显式 task id 时，仅在恰好一个任务等待时自动选择。多个任务等待时不猜测最新任务，而是要求用户发送 `/task <任务ID>`。

## 5. 连接器扩展

`ConnectorRegistry` 是平台唯一注册入口。定义包含：

- 平台枚举和展示信息。
- 面向前端的配置字段。
- 能力集合，如 login、binding、cursor、ack。
- 构造连接实例的 Factory。

新增飞书或钉钉时实现 `Connector` 契约并注册 `ConnectorDefinition`。Manager 不包含平台 `if/elif`。

企业微信使用 `wecom-aibot-sdk.WSClient` 长连接，不实现旧式企业应用 HTTP 回调。自定义 HTTP 轮询持久化 cursor，并在成功处理后向 `ack_url` 提交消息 id。

## 6. 协议输出

人工在一条消息中完成 reasoning、tool call 和 final。工具不执行、不等待：

- Chat Completions 输出 `reasoning_content`、`tool_calls` 和 `content`。
- Responses 输出 reasoning、function_call 和 message output item。
- Anthropic 输出 thinking、tool_use 和 text content block。

非流式请求一次返回完整结构。流式请求在完整回复落库后按 `STREAM_CHUNK_SIZE` 和延迟范围生成协议事件，因此是可控的伪流式，不是假装实时执行工具。

## 7. 持久化与日志

SQLite 是当前唯一运行数据库。启动调用 `create_all`，随后按环境变量播种管理员，并对 `public_models` 执行一次性幂等默认种子（以 `system_settings` 标记完成；管理员清空目录后重启不会补种）。`GET /v1/models` 只读 `public_models`；`llm_models` 是上游供应商同步目录，仅用于管理员挑选上游模型，不直接对外公开。IM 配置和 LLM Key 使用 `APP_SECRET` 派生的 Fernet Key 加密；API Key 与绑定码只存哈希。

`AuditLog` 记录业务动作，`AppLog` 记录运行故障。连接器、网络和 SDK 异常必须更新连接状态并进入统一日志，不允许静默吞错。

## 8. 前端范围

菜单顺序固定为：控制台、连接 IM、API 管理、LLM 管理、网页回复端、系统设置。系统设置下包含基础设置和用户管理。

本轮仅“连接 IM”是完整页面。默认仍进入控制台，其余页面显示未开放占位。连接页采用浅色、紧凑的若依后台风格；管理员视图与普通用户视图共享列表，但权限操作不同。

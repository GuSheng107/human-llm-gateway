# Human LLM Gateway 产品规格

> 当前状态：架构已确认；进入实现阶段。

## 1. 用户故事

- 调用方使用一个 API Key，以 OpenAI 或 Anthropic 客户端调用模型。
- 每个 Key 的请求只发送到该 Key 绑定的真人和 IM 账号；系统支持很多 Key、真人和 IM。
- 真人在 Telegram/企业微信收到任务，发送一段 DSL；调用方收到 JSON 或伪流式 SSE。
- 管理员可以在后台配置 Key、绑定真人和 IM、模型路由、真实 LLM 和运行参数。
- 人工不可用时，`human_fallback_llm` 按配置转真实 LLM。

## 2. 对外 API 验收标准

| 端点 | 验收标准 |
|---|---|
| `GET /healthz` | 返回服务与数据库状态，不要求 API Key |
| `GET /v1/models` | 只返回管理员为当前 Key 路由发布的模型目录 |
| `POST /v1/chat/completions` | 支持 OpenAI 消息格式、`stream=false/true`、错误结构和 usage |
| `POST /v1/messages` | 支持 Anthropic 消息格式、内容块和 SSE 事件 |
| 内部 IM 回调 | 能按连接、发送者、会话和 external message id 关联任务 |

通用规则：缺失/错误/禁用 Key 返回 401；非法请求返回 400；错误响应包含 `task_id` 且不泄露密钥。所有 SSE 都必须最终发送结束事件。

调用方传入的 `model` 仅用于兼容 OpenAI/Anthropic 客户端，不能覆盖后台路由；实际请求始终使用管理员配置的 `upstream_model`。后台可显式同步供应商 `/models` 目录，支持 OpenAI-compatible 与 Anthropic provider，不写死 Claude、OpenAI、Kimi、MiniMax、DeepSeek、Qwen 的版本号。

## 3. 路由验收

- `human`：创建任务并发送到绑定 IM，等待 `/done`。
- `llm`：调用绑定 ModelRoute 的真实 LLM Adapter。
- `human_fallback_llm`：先人工等待；达到 `human_timeout_s` 后转 LLM，并标记 fallback。
- Key A 永远不能读取或接收 Key B 的任务、会话、模型列表或审计记录。

## 4. DSL 验收

```text
/think
分析用户意图，先查询天气。
/tool get_weather {"city":"上海"}
/reply
上海今天多云，注意带伞。
/done
```

- 输出事件顺序为 `reasoning → tool_call* → final`。
- `/tool` 参数必须是合法 JSON；工具只模拟，不执行真实工具。
- `/done` 之前不结束任务；缺少 `/done` 到超时为止。
- 纯文本是否作为快捷 final 由系统配置控制，默认开启。
- 一条完整 IM 消息只处理一次；重复消息不重复输出。

## 5. 伪流式验收

- 人工完整回复先持久化，再按 chunk 策略切分。
- 每个 chunk 的延迟来自配置范围；测试模式关闭 sleep 并使用固定随机源。
- JSON 模式等待完整事件后一次返回；SSE 模式按协议发送事件。
- reasoning/tool_call 只能来自人工 DSL，不伪造真实模型 token 计数。

## 6. 管理后台验收

1. 登录：单管理员、密码哈希、会话过期和失败限速。
2. API Keys：创建、一次性显示、启用/禁用、绑定真人、绑定 IM、脱敏列表。
3. 真人：昵称、状态、允许的 Key 绑定关系；重绑必须审计。
4. IM 连接：Telegram、企业微信配置；启动/停止/状态/配置形状检查；个人微信明确显示“未实现”。
5. LLM 供应商：名称、协议、base URL、密钥、模型和额外参数；密钥不可回显。
6. 模型路由：Key、模型别名、human/llm/fallback、超时和降级目标。
7. 任务台：任务状态、请求摘要、DSL 原文、事件时间线、错误和 fallback 标记。
8. 系统设置：人工超时、chunk 策略、随机延迟、日志留存。
9. 审计：登录、Key、绑定、凭据配置和任务状态迁移可过滤查询。
10. 网页人工台：管理员可在任务详情输入完整 DSL 并直接回复；与 IM 回复走同一解析/幂等链路，显示 `source=web`。

## 7. 任务状态验收

必须覆盖：`received`、`authenticated`、`routed`、`human_waiting`、`llm_streaming`、`pseudo_streaming`、`tool_pending`、`completed`、`timeout`、`cancelled`、`failed`。

- 客户端断开可取消任务；已完成任务不可被重复回复覆盖。
- 绑定 IM 离线时任务保持等待并在超时后按路由处理。
- 非绑定真人消息被拒绝/忽略并记录审计。
- 管理员网页回复只允许操作当前任务所属 Key 的任务，并必须写入审计。

## 8. 个人微信限制

V1 不接入或伪造个人微信已连接状态，只保留配置模型和 Sidecar 契约。个人微信依赖 Windows 已登录客户端或非官方协议，存在版本、登录和风控风险；发布验收不得把占位功能标为可用。

## 9. 测试验收矩阵

| 编号 | 场景 | 证据 |
|---|---|---|
| T1 | OpenAI JSON/SSE | 协议结构与结束事件测试 |
| T2 | Anthropic JSON/SSE | content block 与事件测试 |
| T3 | Key 绑定隔离 | A/B Key 互不可见测试 |
| T4 | DSL 完整流程 | reasoning/tool/final 顺序测试 |
| T5 | 非法 DSL | 错误回执、状态和审计测试 |
| T6 | 人工超时 | timeout 与 fallback 测试 |
| T7 | 伪流式 | 固定随机源、零睡眠、可复现 chunk 测试 |
| T8 | 幂等 | 重复 IM 消息不重复完成任务 |
| T9 | 鉴权脱敏 | 401、Key hash、凭据密文和日志检查 |
| T10 | 后台 CRUD | Key/真人/IM/LLM/路由 CRUD 集成测试 |
| T11 | Connector fake | 发送、接收、离线和健康状态测试 |
| T12 | 个人微信 | 界面/API 明确未实现，无假成功 |

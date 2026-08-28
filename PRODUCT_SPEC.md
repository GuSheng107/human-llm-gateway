# Human LLM Gateway 产品规格

## 1. 产品目标

调用方像调用真实 LLM 一样使用 OpenAI 或 Anthropic SDK；回复内容实际由人类完整输入。系统负责把人工内容转换为 reasoning、模拟 tool call、final 和伪流式事件。

真实 LLM 路由暂时保留，但人工伪装 LLM 是核心用途。

## 2. 协议验收

| 端点 | 要求 |
|---|---|
| `POST /v1/chat/completions` | OpenAI Chat JSON 与 SSE |
| `POST /v1/responses` | OpenAI Responses JSON 与语义事件 SSE |
| `POST /v1/messages` | Anthropic content blocks 与 SSE |
| `GET /v1/models` | 返回数据库 `public_models` 表中启用状态的公开模型（运行时真源），按管理员排序输出 |

公开模型目录由管理员通过 `/admin/models` 接口配置：列表、新增、修改（模型 ID、owned_by、排序、启停）与删除，全部要求管理员权限、模型 ID 唯一，并写入审计日志。`app/model_catalog.py` 中的 34 个默认模型只是全新数据库首次启动的一次性种子；种子幂等，管理员删除或停用全部模型后重启不会补回。上游供应商同步回来的 `llm_models` 不是对外公开目录，`GET /v1/models` 不读取它。

调用方传入的 `model` 用于 SDK 兼容；实际真实 LLM 模型由管理员路由的 `upstream_model` 决定。所有流式响应必须发送各自协议的完整结束事件。

## 3. 用户 Bot 验收

- 普通用户可以创建、查看、启动、停止、登录、绑定和删除自己的 Bot。
- 管理员不能创建或绑定自己的 Bot。
- 管理员能查看所有用户 Bot，并执行状态检查、启动、停止和删除。
- 管理员不能读取用户扫码登录数据。
- 每个 Bot 必须归属一个普通用户，不允许空 owner 或历史连接分支。
- Bot 凭据必须密文落库且不通过列表接口回显。
- 用户通过一次性 `/bind CODE` 将平台 userid 绑定到自己的 Bot。
- 非绑定 userid 的消息不能完成任务，并写入审计。

当前平台为微信 iLink、企业微信 AI Bot、自定义 Webhook、自定义 WebSocket 和自定义 HTTP。

## 4. 任务与幂等

- 一条 API 请求创建一个 `RequestTask`。
- 连接只有一个等待任务时，绑定用户可直接回复。
- 多个任务同时等待时必须提供 `reply_to_task_id` 或首行 `/task <任务ID>`。
- 不允许根据“最新任务”猜测目标。
- `connector_id + external_message_id` 在全局唯一，同一消息不能被第二个任务消费。
- 完整人工回复持久化后才开始伪流式。

## 5. DSL

```text
/think
分析过程
/tool lookup {"id": 1}
/reply
最终回复
/done
```

事件顺序为 reasoning、零个或多个 tool call、final。`/tool` 参数必须是 JSON；工具调用只模拟，不执行，也不等待结果。纯文本快捷回复由 `ALLOW_PLAIN_HUMAN_REPLY` 控制。

## 6. 管理台

菜单顺序：

1. 控制台
2. 连接 IM
3. API 管理
4. LLM 管理
5. 网页回复端
6. 系统设置：基础设置、用户管理

默认页面是控制台。本轮仅连接 IM 页面实现完整交互，其他页面只显示占位，不展示旧功能。

## 7. 初始化与分发

- Python 依赖由 `uv.lock` 固定，必须支持 `uv sync --locked` 和 `uv build`。
- 数据库不存在时自动创建并写入管理员账号密码。
- 不使用迁移框架，不实现旧数据库兼容。
- 前端依赖使用精确版本和 `package-lock.json`。
- 运行日志归一化，连接器错误不能静默。

## 8. 当前验收矩阵

| 场景 | 自动验证 |
|---|---|
| 数据库与管理员自动初始化 | 临时 SQLite 集成测试 |
| 用户自建 Bot 与管理员监管 | 角色权限集成测试 |
| 一次性绑定与 sender 校验 | Webhook/WebSocket 集成测试 |
| 多任务精确路由与全局幂等 | 双任务消息测试 |
| 企业微信 SDK | Mock WSClient 长连接测试 |
| HTTP cursor/ACK 数据契约 | 连接器单元测试 |
| 三种协议 JSON/SSE | reasoning/tool/final 协议测试 |
| 日志与审计 | 数据库查询测试 |
| Python 分发与前端构建 | `uv build`、`npm run build` |

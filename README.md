# Human LLM Gateway

Human LLM Gateway 对外表现为真实 LLM API，实际回复可以由用户在自己的 IM Bot 中完整输入。服务收到完整回复后再生成 JSON 或伪流式 SSE，并支持人工 reasoning、模拟 tool call 和最终文本。

当前协议入口：

- OpenAI Chat Completions：`POST /v1/chat/completions`
- OpenAI Responses：`POST /v1/responses`
- Anthropic Messages：`POST /v1/messages`
- OpenAI 模型目录：`GET /v1/models`

## 启动

后端依赖由 `uv.lock` 固定：

```powershell
Copy-Item .env.example .env
uv sync --locked --extra dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端开发：

```powershell
Set-Location admin
npm ci --registry=https://registry.npmmirror.com
npm run dev
```

生产构建后，FastAPI 会自动托管 `admin/dist`：

```powershell
Set-Location admin
npm run build
Set-Location ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 数据库初始化

项目不使用迁移框架，也不兼容旧表结构。首次运行时会：

1. 自动创建 SQLite 目录和数据库文件。
2. 直接按当前 SQLAlchemy 模型创建全部表。
3. 使用 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 写入管理员账号。
4. 对 `public_models` 做一次幂等默认种子（34 个公开模型，仅全新数据库生效）。
5. 只保存管理员密码哈希，不保存明文密码。

已有同名管理员时不会用环境变量覆盖密码。当前阶段以全新数据库为前提，模型变化后应重新初始化数据库，而不是执行兼容补列。

## 用户 Bot

- 普通用户登录后创建并维护自己的 Bot。
- 管理员不能创建或绑定自己的 Bot，但能查看、启动、停止和删除所有用户 Bot。
- Bot 凭据整体加密后写入 `im_connections.config_json`，列表接口不回显配置。
- 用户在网页生成一次性绑定码，再用本人 IM 账号向 Bot 发送 `/bind CODE`。
- 绑定成功后，只接受该平台 userid 的回复。
- 同时存在多个待回复任务时，回复首行必须是 `/task <任务ID>`。
- `connector_id + external_message_id` 全局幂等，消息不会被两个任务重复消费。

当前可创建的平台：

- 微信 iLink：扫码登录、消息监听与回复。
- 企业微信智能机器人：`wecom-aibot-sdk` WebSocket 长连接。
- 自定义 Webhook：HTTP 入站与任务出站。
- 自定义 WebSocket：带 Token 的双向通道。
- 自定义 HTTP：游标轮询、任务推送和可选 ACK。

新增飞书、钉钉等平台时，在连接器注册表增加定义和实现即可，不需要修改 Manager 分支。

## 人工回复 DSL

```text
/think
先分析用户问题。
/tool lookup {"id": 1}
/reply
这是最终回复。
/done
```

`/tool` 只生成协议中的 tool call，不执行工具，也不等待 tool result。系统先持久化完整事件，再按配置的 chunk 大小和延迟伪流式输出。

## 模型与真实 LLM

`GET /v1/models` 的数据只来自数据库表 `public_models`（管理员运行时配置），不读取任何硬编码常量。`app/model_catalog.py` 中的 34 个默认模型仅作为全新数据库首次启动时的一次性种子：

- 首次初始化写入默认模型并在 `system_settings` 记录完成标记，幂等；
- 之后管理员通过 `/admin/models` 接口列表、新增、修改（模型 ID、owned_by、排序、启停）、删除；
- 管理员删除或停用全部模型后，服务重启不会再次补回默认模型。

“LLM 管理”页面在管理台中仍为占位。`public_models`（对外公开目录）与 `llm_models`（上游供应商同步回来的模型目录）语义不同：前者决定客户端能看到哪些模型 ID，后者只是上游可用模型的缓存，不直接对外公开。真实 LLM 路由继续保留，可配置 OpenAI-compatible 或 Anthropic provider。

## 验证

```powershell
uv run --locked ruff check app tests
uv run --locked python -m pytest -q
uv build
Set-Location admin
npm run build
```

测试不会连接真实微信、企业微信或真实 LLM。

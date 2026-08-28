# Human LLM Gateway

Human LLM Gateway 对外表现为真实 LLM API，对内允许每个用户在 Web 或自己的 IM 中亲自生成回复。用户也可以配置真实 LLM，用它生成可编辑草稿、直接转发请求，或在人工超时后降级转发。

核心区别是：**Fake Model 只代表对外身份，真实 LLM 配置只代表用户私有上游，两者不绑定。**

## 许可证

本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0-only）**，完整文本见仓库根目录 `LICENSE`。

AGPL-3.0 在 GPL-3.0 的 copyleft 基础上增加了 section 13“Remote Network Interaction”条款：任何对程序进行修改并通过计算机网络让用户与其修改版远程交互者，必须向这些远程交互用户提供获取对应源码（Corresponding Source）的机会。本项目选择 AGPL-3.0 的目的是确保修改版在通过网络向用户提供功能时，远程交互用户仍有机会取得相应的 Corresponding Source，从而降低修改版被作为闭源网络服务长期封闭运行的可能性。AGPL 不禁止商业使用或销售；copyleft 约束的是源码开放义务，而不是经营行为。

## 项目状态

项目正在按 `docs/ROADMAP.md` 分阶段重构：

- M0 已完成：收口现有 FastAPI、React、连接器和三协议可运行基线。
- M1 已完成并完成边界修订：固化产品、架构、API、数据库、UI、阶段依赖和开发规范。
- M2 已完成：原子切换到目标领域模型、20 张目标表、安全基础、服务和前端骨架。
- M3 已完成：交付用户注册与治理、邀请码生命周期、强制改密、会话 capability 和权限闭环。
- 下一阶段是 M4：实现目标 IM 连接与任务投递闭环。

当前运行时已不包含 `HumanOperator`、Provider、`ModelRoute` 或旧接口兼容层。现阶段可运行范围是初始化、登录/登出、邀请码注册、个人账号设置和管理员用户治理；IM、API Key、LLM 和三协议推理运行时仍按后续里程碑交付。请勿把当前版本作为生产就绪版本。

## 目标能力

- 外部支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages。
- 用户通过管理员签发的邀请码注册，或由管理员直接创建账号。
- 用户创建自己的 IM 连接、API Key、LLM 配置和私有 Fake Model。
- 管理员维护系统 Fake Model；用户私有 Fake Model 对其他普通用户不可见。
- API Key 决定请求所有者、Web/IM 入口、回复策略、超时和有效 Fake Model。
- 模型分组先预筛候选模型，API Key 可继续选择具体模型；Key 不选择模型代表允许全部候选模型。
- `/v1/models` 必须使用 API Key 鉴权，不提供匿名模型目录。
- 每个用户固定最多 10 个活动任务，所有 Key 和策略共享；第 11 个请求返回协议兼容的 429。
- 人工提交完整 reasoning、假 tool call 和最终文本后，系统再返回 JSON 或伪流式 SSE。
- IM DSL 与 Web 编辑器共享同一个 ReplyDraft；提交前可预览编辑，首个有效提交后不可撤销或覆盖。
- 假 tool call 只作为协议内容返回，不执行也不等待。
- 同协议真实 LLM 转发保留调用方全部字段；跨协议无法等价转换的字段明确返回 400。
- OpenAI Responses 的 `previous_response_id` 由网关在同一 API Key 范围内解析历史响应并等价展开。
- 管理员禁用用户会立即撤销会话和 Key、终止活动任务并释放名额。
- 所有外部响应使用调用方请求的 Fake Model 身份，不暴露人工、IM、fallback 或真实上游。

完整边界见 [产品定义](docs/PRODUCT.md)。

## 目标推理入口

| 格式 | 入口 |
| --- | --- |
| OpenAI 模型目录 | `GET /v1/models` |
| OpenAI Chat Completions | `POST /v1/chat/completions` |
| OpenAI Responses | `POST /v1/responses` |
| Anthropic Messages | `POST /v1/messages` |

这些入口是目标契约，将在 M5-M8 分阶段挂载；M3 运行时尚未开放 `/v1/*` 推理接口。

目标 `/v1/models` 只返回当前 API Key 的有效 Fake Model 集合。有效集合依次受用户可见范围、可选模型分组和 Key 直接模型选择限制，与真实 LLM 配置无关。

调用 `/v1/models` 前必须配置 API Key；匿名请求稳定返回 401。第三方 SDK 或适配器应在模型发现前设置 `Authorization: Bearer <API_KEY>`。

## 快速启动

要求 Python 3.12、`uv`、Node.js 和 npm。后端依赖由 `uv.lock` 锁定，前端依赖由 `package-lock.json` 锁定。

### 后端

```powershell
Copy-Item .env.example .env
uv sync --locked --extra dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动前必须修改 `.env` 中的 `APP_SECRET` 和 `ADMIN_PASSWORD`。

### 前端开发

```powershell
Set-Location admin
npm ci
npm run dev
```

### 生产式本地运行

```powershell
Set-Location admin
npm run build
Set-Location ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

构建后 FastAPI 自动托管 `admin/dist`。默认登录后进入“控制台”；目标菜单顺序为控制台、连接 IM、API 管理、LLM 管理、用户网页回复端、系统设置。

## 数据库初始化

项目不使用迁移框架，也不兼容旧表结构。数据库文件不存在时，应用会直接按当前 SQLAlchemy 模型创建表，并使用环境变量创建管理员、写入默认模型种子。

当前阶段的规则：

1. 自动创建 SQLite 目录和数据库文件。
2. 管理员密码只保存 Argon2id 哈希，不保存明文。
3. 已有同名管理员时，普通重启不使用环境变量覆盖密码。
4. 默认模型种子只在新数据库初始化时写入，管理员后续删除或停用的模型不会在普通重启时补回。
5. Schema 发生不兼容变化时重新初始化数据库，不执行自动补列或旧数据转换。

当前目标表结构和原子事务见 [数据库设计](docs/DATABASE.md)。

## 目标 IM 能力（M4）

M4 将按当前目标领域重新实现以下连接器：

- 微信 iLink：扫码登录、消息监听与回复。
- 企业微信智能机器人：`wecom-aibot-sdk` WebSocket 长连接。
- 自定义 Webhook：HTTP 入站与任务出站。
- 自定义 WebSocket：带 Token 的双向通道。
- 自定义 HTTP：游标轮询、任务推送和可选 ACK 的连接器能力。

普通用户将创建并绑定自己的连接。管理员不能创建、登录或绑定连接，但可查看非敏感状态，并按权限启动、停止、检查和删除用户连接。

新平台通过连接器注册表和统一接口扩展；未来接入飞书、钉钉时，核心任务服务不增加平台条件分支。M2 原子切换已删除 M0 旧连接器实现，当前 `app/connectors` 只保留包边界。

## 目标人工回复 DSL（M6）

M6 将实现以下完整消息 DSL：

```text
/think
先分析用户问题。
/tool lookup {"id": 1}
/reply
这是最终回复。
/done
```

`/tool` 只生成响应中的 tool call。系统会先持久化完整事件，再按协议输出非流式结果或伪流式事件。DSL 解析器和 Web 编辑器将使用同一个 ReplyDraft JSON Schema，并把 `/done` 作为不可撤销的最终提交。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [开发强制规范](AGENTS.md) | 项目边界、代码规范、安全和质量门禁 |
| [贡献指南](CONTRIBUTING.md) | 环境、工作流、测试和完成定义 |
| [产品定义](docs/PRODUCT.md) | 角色、领域语言、核心流程和不可破坏约束 |
| [目标架构](docs/ARCHITECTURE.md) | 分层、依赖、状态机、连接器和转发流程 |
| [API 契约](docs/API_CONTRACT.md) | 管理 API、三种推理格式、流式和错误 |
| [数据库设计](docs/DATABASE.md) | 表、字段、索引、Secret 和原子事务 |
| [UI 规范](docs/UI_GUIDE.md) | Tailwind 浅色 RuoYi 风格、菜单和页面交互 |
| [实施路线图](docs/ROADMAP.md) | M0-M12 进度唯一事实来源 |

## 项目结构

```text
app/                 FastAPI 后端
├── api/             HTTP/WS 边界
├── connectors/      IM 适配器和运行时
├── protocols/       OpenAI/Anthropic 输出适配
└── services/        当前用例服务；后续按目标领域继续拆分
admin/               React + Tailwind 管理后台
docs/                产品、架构、契约和路线图
tests/               后端自动化测试
```

目标后端进一步拆为 `domain`、`services`、`repositories`、`connectors`、`protocols` 和 `core`，依赖方向固定为 API → Service → Repository/Domain。

## 验证

仓库根目录：

```powershell
uv lock --check
uv run --locked ruff format --check app tests
uv run --locked ruff check app tests
uv run --locked python -m pytest -q
uv build
git diff --check
```

前端：

```powershell
Set-Location admin
npm ci
npm run build
```

测试使用替身，不连接真实微信、企业微信、用户 LLM 或生产数据库。实际页面视觉由用户完成最终验收。

生产部署、CI、SQLite 在线备份恢复、日志轮转、`/readyz` 和优雅关闭将在 M10 完成；M11 执行发布验收。隔离工具沙箱延期到 M12，不阻塞首版。

## 安全提示

- 不要提交 `.env`、数据库、日志、构建缓存、二维码、完整 API Key 或任何真实 Secret。
- 管理员不能查看用户密码、完整 API Key、LLM Secret、IM Token 或登录二维码，也不能替用户回复。
- 调用方提交的工具永远不会由当前系统自动执行。
- 后续工具执行只会在管理员白名单、用户权限、显式确认和隔离沙箱同时满足时开放。

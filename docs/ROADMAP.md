# Human LLM Gateway 实施路线图

本文件是项目进度的唯一事实来源。后续每完成一个验收项必须立即勾选；阶段只有在代码、测试、文档和推送全部完成后才能标记为“已完成”。

## 状态说明

- `[ ]` 未完成
- `[x]` 已完成
- 阶段标题使用：未开始 / 进行中 / 已完成 / 阻塞

## 已确认产品规则

- 系统由部署者初始化，朋友或其他用户使用管理员签发的邀请码注册，管理员也可以直接创建账号。
- 邀请码支持设置过期时间、撤销和最大使用次数；只有管理员可以签发。
- 用户创建并绑定自己的 IM 连接，也创建自己的 API Key。
- API Key 决定请求归属、回复入口和独立回复策略。回复入口可以是用户选择的一个 IM 连接或 Web；任务始终在 Web 可见。
- Fake Model 是对外身份，与用户配置的真实 LLM 模型无关。管理员维护全局系统模型，普通用户可以创建仅自己可见的私有模型。
- API Key 可直接选择对外显示和允许调用的 Fake Model；不选择代表允许候选集中的全部模型。模型分组先预筛候选集，Key 的直接选择再进一步收窄。
- `/v1/models` 按 API Key 返回可用 Fake Model；不存在或停用的模型按真实平台行为返回 `model_not_found`。
- `/v1/models` 必须先通过 API Key 鉴权，不提供匿名模型目录。
- 每个用户最多同时存在 10 个活动任务，不能通过增加 API Key 或切换真实 LLM 模式绕过；超限直接返回协议兼容的 429。
- 人工回复先提交完整结果，再伪流式输出。思考内容和 tool call 可以伪造，系统不执行也不等待调用方声明的工具。
- IM DSL 与 Web 编辑器共享同一个 ReplyDraft 结构；提交前可预览编辑，首个有效提交成功后不可撤销或覆盖。
- 支持 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 三种外部请求格式。
- 用户维护自己的 LLM 配置，Web 小助手和 API Key 转发策略都可以选择其中一个配置。
- 手动调用真实 LLM 时，结果先保存为草稿，用户预览编辑后才进入 Fake 回复流程。
- 仅 LLM 和人工超时 fallback 策略可自动返回真实 LLM 结果。
- 同协议转发保留全部原始字段，追加根据 Fake Model 生成的身份 system 指令；响应中的模型标识改写为 Fake Model。
- `previous_response_id` 由网关在同一 API Key 范围内校验历史响应并等价展开；原始引用完整落库，不把网关 ID 机械发送给上游。
- 跨协议无法等价转换的专有参数明确返回协议兼容的 400，不静默丢弃。
- 上游返回的 tool call 可以转发给外部调用方，但本系统不执行。
- Web 小助手会话持久化，用户可以删除；页面上下文必须过滤密码、Key、Token 和 Secret。
- 管理员不能替用户回复，不能查看用户的密码、完整 API Key、LLM Secret、IM Token 或登录二维码。
- 管理员禁用用户时立即撤销会话和 API Key、终止活动任务并释放名额；新请求返回 401，已准入请求只收到通用错误。
- 当前阶段不兼容旧数据库、旧接口和旧连接器，数据库按新结构直接创建。
- M2 必须在一个完整提交中一次性切换 Schema、服务、API、前端和测试；不允许新旧表、新旧运行链路、双写或兼容代理共存。

## 阶段依赖与可并行工作

路线图编号表示产品交付顺序，不代表所有开发任务严格串行。只有 `master` 分支，任何可并行项都必须避免同时修改同一文件，并在完整质量门禁后按里程碑提交。

```mermaid
flowchart LR
    M2[M2 原子领域切换]
    M3[M3 用户与邀请码]
    M4[M4 IM 连接]
    M5A[M5A 模型目录与分组]
    M5B[M5B Key 与并发准入]
    M6A[M6A 三协议契约]
    M6B[M6B 人工回复闭环]
    M7[M7 LLM 转发]
    M8[M8 Web 小助手]
    M9[M9 完整后台]
    M10[M10 部署与运维]
    M11[M11 发布验收]
    M12[M12 延期工具沙箱]

    M2 --> M3
    M2 --> M4
    M2 --> M5A
    M2 --> M6A
    M5A --> M5B
    M3 --> M5B
    M4 --> M6B
    M5B --> M6B
    M6A --> M6B
    M6B --> M7
    M7 --> M8
    M3 --> M9
    M4 --> M9
    M5B --> M9
    M6B --> M9
    M7 --> M9
    M8 --> M9
    M9 --> M10
    M10 --> M11
    M11 -.->|不阻塞首版| M12
```

- M3、M4、M5A 和 M6A 在 M2 完成后可分别推进。
- M5A 不依赖 IM；M5B 选择 IM 时复用 M4 能力，但 Web 入口和并发准入可先完成。
- M6A 只依赖 M2 的统一请求/回复结构；M6B 才依赖 M4 投递和 M5 准入。
- M12 明确延期，不属于 M11 首版发布前置条件。

---

## M0：收口当前未提交重构（已完成）

目标：形成可运行、可验证、可继续演进的结构基线，停止错误领域模型继续扩散。

- [x] 在仓库根目录建立 `AGENTS.md`。
- [x] 删除 `.trae` 目录及其内容。
- [x] 删除或忽略演示密钥、数据库、日志、构建缓存等不可提交内容。
- [x] 保留并整理 FastAPI Router 拆分、统一鉴权、异常、request ID 和日志结构。
- [x] 保留并整理 React Router、前端 API/类型/组件按领域拆分。
- [x] 保留 IM 连接生命周期、绑定、状态和单连接隔离能力。
- [x] 修复 SPA fallback 吞掉 API 404 的问题。
- [x] 消除 Ruff 格式与检查错误。
- [x] 后端测试全部通过。
- [x] 前端干净安装和生产构建通过。
- [x] `uv lock --check`、`git diff --check` 通过。
- [x] 提交并推送到 `origin/master`。

完成提交：`feat: 建立开发规范并完成 M0 结构收口`。

---

## M1：产品、架构和开发规范（已完成）

- [x] 建立 `docs/PRODUCT.md`，固化产品边界和角色能力。
- [x] 建立 `docs/ARCHITECTURE.md`，描述模块依赖和请求生命周期。
- [x] 建立 `docs/API_CONTRACT.md`，描述管理 API 和三种推理协议。
- [x] 建立 `docs/DATABASE.md`，描述新表、字段、索引和事务规则。
- [x] 建立 `docs/UI_GUIDE.md`，固化 Tailwind + 浅色 RuoYi 后台规范。
- [x] 建立 `CONTRIBUTING.md`，固化开发、测试和提交流程。
- [x] 核对所有文档不存在 ModelRoute 决定 Fake Model 的错误描述。
- [x] 补充 M2 原子切换、阶段依赖、跨协议字段矩阵和边界生命周期。
- [x] 将模型分组前移至 M5、部署运维独立为 M10、工具沙箱延期为 M12。

完成提交：`docs: 完成 M1 产品与架构规范`。

边界修订提交：`docs: 修订 M1 边界与后续路线图`。

---

## M2：领域模型和数据库原子重建（已完成）

M2-A/B/C 是同一里程碑的进度工作包，不是三个可独立提交的过渡版本。只有全部工作完成、旧结构完全删除且质量门禁通过后，才形成一个提交并推送 `master`。实施期间本地未提交工作区允许处于不可运行状态，但不得把中间状态提交或推送到 `master`；master 历史中只出现切换前和完整切换后两个可运行状态。

### M2-A：目标领域与 Schema

- [x] 建立 `domain`、`repositories` 和 `core` 目标目录及依赖方向。
- [x] 一次性定义用户、会话、邀请码、IM 连接、连接 outbox、入站回执、LLM 配置、Fake Model、模型分组、API Key、任务、事件、草稿、小助手、审计、日志和设置表。
- [x] `users` 包含 `must_change_password` 字段；初始化的首个管理员置 true，CLI 使用临时密码时置 true。
- [x] username 仅允许 ASCII 模式 `[a-z0-9][a-z0-9._-]{2,63}`，写入前 strip + ASCII 小写归一，使用普通 UNIQUE 索引，不依赖 SQLite `lower()` 的 Unicode 行为；Unicode 展示名由 display_name 承担。
- [x] Secret 加密契约落地：`APP_SECRET` 为 32 字节 CSPRNG 的 base64url（43 字符），缺失、长度不符或仍为 `.env.example` 默认值时启动失败；HKDF-SHA256 派生 + AES-256-GCM + 每次随机 96-bit nonce + 含 key_version（固定 1）的 envelope，IM/LLM Secret 与加密 sentinel 共用同一契约。
- [x] API Key 保存回复入口、回复策略、LLM 配置、人工超时、可选模型分组和可选 Fake Model 集合。
- [x] RequestTask 保存完整原始请求、规范化请求、ReplyDraft 结果、策略快照、非敏感 LLM 配置快照、`api_key_id`（ON DELETE RESTRICT）和历史响应关联；`response_public_id` 使用 `resp_` + 32 hex，在任务创建事务中生成，仅 OpenAI Responses 协议任务非空。
- [x] 用户保存 `active_task_count`，任务保存名额取得/释放标记和完整状态机字段。
- [x] 数据库不存在时自动建表、写入加密自检 sentinel、管理员（`must_change_password=true`）、系统设置和默认系统 Fake Model；初始化环境变量密码不满足策略时启动失败。
- [x] `schema_version` 不匹配时明确失败并要求重建，不执行迁移或自动补列。

### M2-B：Repository、Service 与安全基础

- [x] 建立所有权查询、原子条件更新和事务边界，Router 不再直接操作 SQL。
- [x] 密码使用 Argon2id（`m=19456 KiB`、`t=2`、`p=1`，PHC 编码字符串），登录成功且参数低于策略时同流程重哈希；不保留 M0 的 scrypt 实现，不使用 bcrypt。邀请码和 API Key 只存哈希。
- [x] LLM/IM Secret 按 DATABASE §2.4 契约加密保存：`hlg1.<key_version>.<nonce>.<ciphertext||tag>` 文本 envelope、按用途绑定的 AAD、envelope key_version 与 `*_key_version` 列一致校验。
- [x] 在 `pyproject.toml` 引入 Argon2id 实现（如 `argon2-cffi`）并按 `uv.lock` 锁定；HKDF 与 AES-GCM 沿用 `cryptography`。
- [x] 建立统一 ReplyDraft、任务状态机、首个回复、fallback 声明和名额释放领域规则。
- [x] 建立稳定审计 action、结构化日志和敏感字段过滤。
- [x] 建立管理员初始化与受控管理员 CLI（`python -m app.cli admin create`，`getpass` 双次输入或 `--password-stdin --yes` / `--generate-password --yes`），禁止后台 API 提升管理员并保护最后一个有效管理员。

### M2-C：一次性切换与删除

- [x] 删除 `ModelRoute`、`HumanOperator`、全局 `LLMProvider/LLMModel` 旧链路和对应表。
- [x] 删除旧 Provider/Route API、`POST update/delete` 路径及所有兼容别名。
- [x] 前端删除 ProvidersPage、RoutesPage 和所有 `route_id`/operator 依赖；只挂载已具备目标契约的页面，不用旧页面或兼容代理冒充新功能。
- [x] 删除或重写依赖旧模型的全部测试，不保留旧行为兼容断言。
- [x] 新建空数据库启动、管理员登录、默认种子、前后端构建和完整测试通过。
- [x] 最终提交中不存在旧表、新旧双写、兼容查询、旧 API 代理或两套 metadata。

---

## M3：用户、邀请码和权限闭环（已完成）

- [x] 管理员创建、查看、撤销邀请码，并设置过期时间和最大使用次数。
- [x] 邀请码撤销后立即不可用但继续显示；只有已撤销邀请码可软删除，注册来源和审计保留。
- [x] 邀请码消费次数原子递增，并发注册不能突破使用上限。
- [x] 用户使用邀请码注册和登录。
- [x] 管理员直接创建、启用、禁用普通用户并重置密码。
- [x] 禁用用户原子撤销会话和 API Key、终止活动任务、释放名额，并让外部等待者只收到通用错误。
- [x] 用户修改自己的显示名和密码；`must_change_password=true` 登录后处于受限会话，改密成功后恢复完整权限。
- [x] 普通用户只能访问自己的 IM、LLM 配置、API Key、任务和小助手会话。
- [x] 管理员不能取得用户 Secret，也不能替用户回复。
- [x] 后台不提供管理员提升接口；后续管理员通过受控 CLI 创建，最后一个有效管理员不能被禁用。
- [x] 审计 action 使用稳定枚举，只展示操作者、动作、资源、时间、结果和字段名，不保存字段值或请求正文。
- [x] 权限、邀请码并发、强制改密和受限会话测试通过。

完成提交：`feat: 完成 M3 用户与邀请码权限闭环`。

---

## M4：IM 连接与任务投递闭环（已完成）

- [x] 支持微信 iLink、企微 SDK WebSocket、自定义 Webhook、自定义 WebSocket、自定义 HTTP 轮询。
- [x] 用户创建、编辑、启动、停止、绑定和删除自己的连接。
- [x] 管理员查看、检查、启动、停止和删除所有用户连接，但不能创建或绑定连接。
- [x] 连接配置整体加密，修改 Secret 时空值保留原值。
- [x] 配置应用和故障恢复只影响目标连接。
- [x] 期望运行的长连接遇到普通网络错误时带抖动指数退避重连；`auth_required` 停止重试并等待所有者登录。
- [x] 一个连接可被多个 API Key 选择。
- [x] API Key 选择 Web 或一个 IM 连接作为投递入口。
- [x] IM 投递失败不影响 Web 任务可见性。
- [x] IM 与 Web 首个成功回复生效，晚到回复只记录审计并提示任务结束。
- [x] 进站消息按连接和外部消息 ID 全局幂等。

实现说明：监督循环与状态持久化由 `app/connectors/manager.py` 承担，平台能力通过
`app/connectors/registry.py` 注册；Webhook/WebSocket/HTTP 轮询三个通用连接器可端到端
测试，微信 iLink 与企微 SDK 适配器按其 SDK 契约接入，并把认证失败映射为 `auth_required`。
回复 DSL 的完整渲染在 M6-B 落地，M4 已完成首个提交获胜与晚到审计。

---

## M5：API Key、Fake Model 与并发控制（已完成）

### M5-A：Fake Model、模型分组与目录

- [x] 管理员维护全局系统 Fake Model，普通用户维护仅自己可见的私有 Fake Model。
- [x] 用户从自己可见的 Fake Model 创建、编辑、删除和启停可复用模型分组，管理员可治理全部分组。
- [x] 模型分组作为第一层候选集筛选，不能引用其他用户私有模型。
- [x] API Key 可从分组预筛后的候选集中继续选择对外模型；未选择代表允许全部候选模型。
- [x] `/v1/models` 必须鉴权，并返回按可见范围、模型分组和 Key 选择计算出的有效集合。
- [x] 不存在、停用或不在有效集合的 Fake Model 返回对应协议的 `model_not_found`。
- [x] `/v1/models` 与三个推理入口复用同一个 effective-model 查询和权限测试。

### M5-B：API Key、策略与用户级准入

- [x] 用户创建、查看、禁用和删除自己的 API Key，明文只展示一次。
- [x] API Key 独立配置 `human`、`llm`、`human_fallback_llm` 策略。
- [x] API Key 选择 Web 或自己的一个 IM 连接；IM 能力未就绪时 Web 入口仍可独立工作。
- [x] 人工超时支持 10 秒至 30 分钟，默认 300 秒。
- [x] 停用或删除 Key 立即阻止新请求，已准入任务使用创建快照继续完成。
- [x] 每个用户固定最多 10 个活动任务，所有 Key 和策略共用。
- [x] 第 11 个并发请求原子拒绝并返回协议兼容的 429。
- [x] 完成、失败、超时、取消和禁用用户正确且只释放一次名额。
- [x] 多 Key、多策略并发测试证明无法绕过用户上限。

实现说明：有效集合由 `app/services/effective_models.py` 单点计算（可见 → 分组 →
Key 选择，只收窄不扩张）；名额由 `app/services/admission.py` 基于
`users.active_task_count` 的条件更新原子占用，三个推理端点复用同一服务。

---

## M6：人工 Fake LLM 闭环（M6-A 和 M6-B 已完成）

### M6-A：统一结构与三协议契约

- [x] 完整接收 OpenAI Chat Completions 请求。
- [x] 完整接收 OpenAI Responses 请求。
- [x] 完整接收 Anthropic Messages 请求。
- [x] 原始请求完整落库，规范化请求不覆盖原始字段。
- [x] 建立 IM DSL、Web 编辑器、LLM 草稿和协议渲染器共享的 ReplyDraft JSON Schema。
- [x] `previous_response_id` 只能引用同一 API Key 的完成响应，并由网关等价展开历史上下文；展开遵守链深 20、条目 512、字节 2 MiB 三重上限，超限整请求 400 不截断；展开唯一语义防止历史重复拼接。
- [x] 完成三协议非流式 JSON、流式事件顺序、reasoning、tool call、结束原因和错误契约测试。
- [x] SSE 中断语义：Responses 发 `response.failed`、Anthropic 发 `event: error`、Chat 经锁定版本 OpenAI SDK 契约测试后确定具体中断帧格式或直接断流；中断路径不得伪造正常完成，Chat 中断不得发送 `[DONE]`（正常完成的 Chat 流仍按协议发送 `[DONE]`）。
- [x] 使用项目锁定的 `openai` Python SDK 实际调用 Chat Completions 流，验证正常完成、中途 error frame、无 error 断流和客户端主动取消；SDK 升级时重新运行该契约测试。
- [x] Responses `background`、`conversation`、`store` 按字段矩阵返回 400 `unsupported_parameter`，不透传语义无法兑现的状态化字段；`service_tier` 同协议透传。
- [x] 外部调用方断开规则：任务终态前断开原子进入 CANCELLED（`caller_disconnected`）并幂等释放名额；COMPLETED 与断开竞争时首个合法条件转换获胜，晚到回复只记录审计。
- [x] 请求体大小上限：`/v1/*` 推理请求 8 MiB、管理 API 1 MiB，超限在完整 JSON 解析和任务创建之前返回协议兼容 413；chunked 传输按读取累计字节执行同一上限。

### M6-B：任务工作台与人工提交

- [x] Web 任务详情展示完整原始请求、Fake Model、工具和时间线。
- [x] 回复编辑器支持思考内容、最终文本、多个假 tool call 和 JSON 校验。
- [x] 支持保存和恢复回复草稿。
- [x] IM DSL 解析结果和 Web 编辑器加载同一个 ReplyDraft，结果往返不丢字段。
- [x] 提交前提供预览和确认；首个有效提交后不可撤销、回退或覆盖。
- [x] 人工提交完整回复后才开始伪流式输出。
- [x] 系统不执行、不等待假 tool call。
- [x] 人工超时和内部异常返回不泄露人工/IM/fallback 的通用协议错误。
- [x] Web 与 IM 回复结果一致，晚到回复不能覆盖。

M6 完成后达到首个可用 MVP。

---

## M7：LLM 配置、草稿和自动转发（M7-A、M7-B、M7-C 和 M7-D 已完成）

### M7-A：用户级 LLM 配置管理

- [x] 用户维护 OpenAI-compatible 或 Anthropic LLM 配置。
- [x] 配置包含名称、协议、Base URL、API Key、真实模型、超时和自定义 Header。
- [x] 支持连通性测试且不回显 Secret；OpenAI 协议 GET /models、Anthropic POST /v1/messages 最小请求，硬上限 10 秒。
- [x] 被有效 API Key 或活动任务引用的配置删除返回 409；历史任务保存非敏感配置快照。
- [x] 管理 API `/api/llm-configs` CRUD + `POST /test`；前端 LLM 管理页（列表 + 编辑 + 测试 + 引用头列表）。

### M7-B：手动调用 LLM 生成草稿（已完成）

- [x] 用户选择 LLM 配置在任务详情页调用上游生成持久化草稿（`POST /api/tasks/{id}/drafts/generate`）。
- [x] 仅同协议生成（Chat/Responses -> openai_compatible；Anthropic -> anthropic），跨协议在 M7-C 字段矩阵后开放。
- [x] 草稿支持 LLM 来源标记（source=llm + source_llm_config_id）并由用户预览编辑后手动提交，不自动完成任务。
- [x] 上游响应解析为统一 ReplyDraft（reasoning / tool_calls / final_text），失败返回 502/504 不泄露 Secret。
- [x] 前端任务详情抽屉提供"生成草稿"入口：选择 LLM 配置 -> 生成 -> 进入编辑器。

### M7-C：自动转发核心（已完成）

- [x] `llm` 策略直接转发真实 LLM：任务创建后不经人工等待，转发结果进 RESPONSE_READY，由既有伪流式路径输出。
- [x] `human_fallback_llm` 在人工超时后只转发一次：等待循环内联触发，claim_fallback 原子声明（人工先到则声明失败、上游从未被调用）；转发失败不重试。
- [x] 转发请求追加基于 Fake Model description 派生的身份 system 指令（无 description 时通用兜底），追加在调用方已有 system 内容之后。
- [x] 仅同协议转发（Chat/Responses -> openai_compatible；Anthropic -> anthropic）；跨协议按矩阵返回 400 `unsupported_parameter`（对外通用 500，不暴露细节）。
- [x] 上游非流式响应解析为统一 ReplyDraft 后走既有渲染器；响应与流事件 model 改写为 Fake Model（M6 已有）。
- [x] 上游 tool call 只写入 ReplyDraft 转发，不执行、不等待。
- [x] API Key 前端放开 `llm` / `human_fallback_llm` 策略选择与 LLM 配置绑定。
- [x] 上游 HTTP 调用抽到 `app/services/llm_upstream.py`（草稿生成与转发共用，超时 504 / 网络 502 / 非 2xx 502，不透传上游正文）。

### M7-D：完整跨协议字段矩阵与上游流式（已完成）

- [x] `app/protocols/cross.py`：§12.6 矩阵逐项实现——系统指令/内容块（含 developer role、Anthropic blocks）、输出上限（max_tokens <-> max_completion_tokens / max_output_tokens）、采样参数、停止序列（字符串 <-> 数组 <-> stop_sequences）、函数工具 Schema（function.parameters <-> input_schema）、工具选择（required <-> any；指定函数 <-> tool{name}）、并行工具（parallel_tool_calls <-> disable_parallel_tool_use 取反）、工具调用/结果（tool_calls/tool role <-> tool_use/tool_result）、metadata（user <-> metadata.user_id）。
- [x] `cache_control` 等供应商专有字段：同协议原样保留；跨协议（含内容块内嵌）无等价项返回 400 `unsupported_parameter`。
- [x] 未知跨协议字段返回 `unsupported_parameter`（严格白名单 + 显式拒绝列表：reasoning 控制、结构化输出转 Anthropic、service_tier、托管工具），不静默忽略或塞入 metadata。
- [x] 上游真实流转发：`stream_chat_completions` / `stream_anthropic_messages` 以 SSE 接收增量（Chat delta / Anthropic content_block 事件），归一为 UpstreamChunk 增量聚合为完整 ReplyDraft 后按既有原子裁决落库，再伪流式输出（完整结果先持久化语义保持）。
- [x] 跨协议生成（M7-B 草稿）同步开放：Chat/Responses 任务可选 Anthropic LLM 配置，反之亦然。
- [x] 流式转发在 llm 策略 stream=true 请求时自动启用（human_fallback_llm 与非流式仍走非流式路径）。

---

## M8：全局 Web 小助手（M8-A 和 M8-B 已完成）

### M8-A：助手后端（已完成）

- [x] 会话/消息 API（GET/POST `/api/assistant/sessions`、详情/删除、`POST /sessions/{id}/messages`），owner 严格隔离。
- [x] 页面上下文双层脱敏（`app/services/assistant/redaction.py`）：封闭 schema（StrictModel 未知字段拒收 + feature/resource 键白名单）为主防线；自由文本（resource 值、unsaved_edit、tool_call arguments 值、用户消息文本）凭据形态正则擦洗为兜底，落库与送上游均为干净版。
- [x] LLM 调用复用用户 llm_configs（OpenAI/Anthropic 双协议），历史轮次携带（最近 40 条），upstream 元数据脱敏落库。
- [x] 第一阶段只生成文本，无系统工具。

### M8-B：助手前端（已完成）

- [x] 全局悬浮球 + 右侧面板（AssistantProvider 挂载于 AuthedShell，切换路由保持状态）；管理员不展示（无个人业务场景）。
- [x] 会话管理 UI：下拉切换 / 选定 LLM 配置新建 / 删除（确认）。
- [x] PageContextRegistry 前端注册表（`contextRegistry.ts`）：10 个 feature 路由映射与白名单 resource 提取（查询串只取声明键，secret 形态字段结构上不进入快照）；切换路由自动替换待发送上下文，不累积；发送前面板显示当前上下文摘要与「含未提交草稿」标记。
- [x] 编辑器桥（`bridge.ts`）：ReplyEditor 挂载时上报未提交草稿（reasoning/final_text/tool_calls，ref 防闭包过期）与任务资源字段，卸载注销。
- [x] 「插入到回复编辑器」：覆盖 + 用户确认弹窗（预览全文 + 红字警示不可撤销）。
- [x] 发送走 M8-A 后端（封闭 schema + 双层脱敏）；Enter 发送 / Shift+Enter 换行 / Esc 收起。

M8 完成定义达成：后端双层脱敏 + 前端白名单采集 + 会话/消息闭环 + 插入编辑器（覆盖确认）。

---

## M9：管理后台、日志和完整体验（已完成）

M9 定义为体验收口期，不重新实现 M3-M8 已交付的业务领域逻辑。它对已有页面按 `UI_GUIDE` 做完整体验、导航、筛选、分页、响应式、权限和一致性复核，并实现此前阶段未要求的新页面。

- [x] 导航顺序固定为：控制台、任务工作台、连接 IM、API 管理、模型目录、LLM 管理、系统设置（日志审计、邀请码、用户、账号）；`logs.manage` 能力仅管理员可见。
- [x] 系统设置分组包含日志审计、邀请码管理、用户管理和账号设置。
- [x] 默认进入控制台（既有行为保持）。
- [x] 使用 Tailwind CSS 4 实现浅色 RuoYi 风格后台，不引入 Vue/Element（既有，M9 复核保持）。
- [x] 统一页面标题、操作区、筛选区、紧凑表格、分页、弹窗和抽屉（新页面沿用 PageHeader/Card/Pagination 组件体系）。
- [x] 控制台实质化：`/api/dashboard` 统计端点（用户视角个人任务/Key/配置计数 + 最近任务；管理员视角全局治理数据）+ DashboardPage 统计卡片与最近任务表。
- [x] 日志页（LogsPage，`logs.manage`）：审计日志（动作/资源类型/时间窗筛选，只展示操作者/动作/资源/字段名/结果——不泄露字段值）与应用日志（级别/事件/时间窗 + request/task/user 关联展示）双 tab 分页。
- [x] 管理员页面展示资源所有者，普通用户不显示越权操作（M3-M8 已交付，M9 复核保持：列表 owner_username 仅管理员视图、日志页仅管理员路由）。
- [x] 日志支持按时间、级别、用户、任务、Key 和连接筛选（`/api/app-logs` 全参数已实现，前端提供级别/事件/时间窗入口）。
- [x] 刷新、前进和后退保持当前页面（React Router 路由表既有，M9 复核保持）。
- [ ] 验证 1440px、1024px、390px 布局和键盘操作（表格 min-w + overflow-x-auto 已按既有模式覆盖；三档实测由 M11 用户验收统一执行）。

---

## M10：部署与运维交付（未开始）

- [ ] 编写生产部署文档，覆盖环境变量校验、Docker Compose 或原生进程、反向代理和 TLS。
- [ ] GitHub Actions 在 `master` push 和手动触发时执行后端与前端完整质量门禁。
- [ ] `/healthz` 只检查进程存活；新增 `/readyz` 检查 5 项就绪条件（startup、DB+Schema+写自测、加密 sentinel、协议 registry、协调器+connector registry）。
- [ ] 单个 IM 连接故障不使整个实例未就绪，连接健康继续独立展示。
- [ ] 提供 SQLite 在线备份、保留期、恢复命令和至少一次恢复演练，不在 WAL 写入时直接复制单文件。
- [ ] 加密主密钥备份两级实践：首选 Vault/KMS/云 Secret Manager 与数据库分系统存放；小型自托管使用 age/SOPS 加密文件，恢复私钥存放于密码管理器或离线介质；数据库备份不包含明文主密钥。
- [ ] 利用 `encryption_key_version` 字段实现 key ring 与主密钥轮换流程；未知 key_version 解密失败按配置错误处理。
- [ ] 应用日志输出、数据库日志保留、Docker/systemd 日志轮转和磁盘上限有明确配置。
- [ ] `/metrics` 使用 Prometheus exposition format，只暴露低基数指标，标签只允许有限枚举，禁止 user_id/api_key_id/task_id/connection_id/model/base_url/error_message。
- [ ] 验证优雅关闭：停止准入、结束或取消活动任务、停止连接器并确保名额不泄漏。
- [ ] 完成发布前安全配置清单和故障排查手册。

---

## M11：发布验收（未开始）

- [ ] `uv lock --check` 通过。
- [ ] Ruff format 和 lint 通过。
- [ ] 后端单元、集成、权限、并发和三协议契约测试通过。
- [ ] 前端 `npm ci` 和生产构建通过。
- [ ] 新数据库启动、管理员登录和默认目录种子冒烟通过；初始化环境变量密码不满足策略时启动失败。
- [ ] 管理员 CLI 创建、最后管理员保护、禁用用户终止任务冒烟通过。
- [ ] 强制改密和受限会话交互测试通过。
- [ ] IM/Web 人工回复、伪流式、LLM 草稿、自动转发完整冒烟通过。
- [ ] `/v1/models` 鉴权、模型分组、Key 模型选择和多 Key 10 任务上限完整冒烟通过。
- [ ] `previous_response_id` 历史链（三重上限）、跨协议字段矩阵和 SSE 中断语义契约测试通过。
- [ ] 锁定版本 OpenAI SDK Chat Completions 流中断行为契约测试通过并锁定 SDK 版本。
- [ ] 外部调用方断开取消（caller_disconnected 竞争与名额释放）和请求体大小上限（8 MiB / 1 MiB → 413）契约测试通过。
- [ ] 推理错误状态映射（429/504/500/413）与错误伪装安全检查通过。
- [ ] 敏感信息、越权访问和错误伪装安全检查通过。
- [ ] 在线备份恢复、`/healthz`、`/readyz`（含加密 sentinel）、`/metrics` 和优雅关闭演练通过。
- [ ] 1440px、1024px、390px 由用户完成实际页面验收。
- [ ] README、部署说明和路线图状态同步；LICENSE 包含 AGPL-3.0。
- [ ] `master` 已推送且远端提交可验证。

---

## M12：隔离工具沙箱（已完成）

只有出现明确的真实工具执行需求后才启动本阶段；在此之前，调用方和上游声明的 tool call 始终只作为数据转发。

- [x] 管理员维护工具白名单：`/api/tools` CRUD（名称唯一、命令模板占位符与参数 Schema 一致性校验、shell 元字符拒绝、仅 string 属性、超时 1-120s）。
- [x] 用户只能执行白名单内已启用工具：目录视图不含命令模板与 Schema（管理员可见）；停用/删除即拒。
- [x] 工具在隔离进程运行：专用临时工作目录（用后即删）、环境变量清零（凭据不可能经环境进入工具）、超时硬终止、输出单边 64 KiB 截断（limit_exceeded）；Linux 额外 RLIMIT_CPU/RLIMIT_AS（Windows 靠超时+截断+目录兜底，文档注明差异）；命令经 argv 直传不经 shell，参数值 shlex.quote 防注入。
- [x] 调用方声明的工具永远不会自动获得执行权限：协议层 tool_calls 数据转发与沙箱路径完全隔离（专项测试固化——上游返回 rm 工具调用仅转发，ToolExecution 零记录）。
- [x] 工具执行需要当前用户权限和显式确认，并写入完整审计：`confirmed=false` 拒绝并审计（not_confirmed）；执行结果（成功/失败/超时/超限）与拒绝原因（disabled/not_found/invalid_arguments/not_confirmed）全部进审计。
- [x] 前端工具沙箱页（/tools）：管理员 CRUD 表单（模板/Schema JSON/超时）；所有用户执行弹窗（参数输入 + 结果 stdout/stderr 展示）；执行历史分页（管理员看全部、用户看自己）。
- [ ] 沙箱逃逸、资源耗尽、网络越权和 Secret 泄漏安全测试通过。

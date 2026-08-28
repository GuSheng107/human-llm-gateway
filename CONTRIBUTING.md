# 参与 Human LLM Gateway 开发

本项目当前处于全栈重构阶段。开始修改前，请先阅读根目录 `AGENTS.md`；它是开发和编码代理必须遵守的最高优先级仓库规范。

## 0. 许可证

本项目采用 **AGPL-3.0-only**，完整文本见仓库根目录 `LICENSE`。贡献者提交的代码默认按同一许可证授权。在贡献前请确认你理解以下约束：

- 你提交的代码必须是你自己拥有版权，或你有权按 AGPL-3.0 授权的作品。
- 一旦合并，对程序的修改版在通过网络与用户远程交互时，必须按 section 13 向这些远程交互用户提供获取对应源码（Corresponding Source）的机会。
- 不要引入与 AGPL-3.0 冲突的依赖；如需引入新依赖，请在 PR 中说明其许可证及兼容性。

## 1. 事实来源

文档职责如下：

| 文档 | 作用 |
| --- | --- |
| `AGENTS.md` | 强制开发规范、边界和质量门禁 |
| `docs/PRODUCT.md` | 产品角色、术语、流程和不可破坏约束 |
| `docs/ARCHITECTURE.md` | 目标模块、依赖方向、状态机和请求生命周期 |
| `docs/API_CONTRACT.md` | 管理 API、推理协议和错误契约 |
| `docs/DATABASE.md` | 目标表结构、索引、事务和初始化规则 |
| `docs/UI_GUIDE.md` | Tailwind 浅色后台和页面交互规范 |
| `docs/ROADMAP.md` | 阶段进度唯一事实来源 |

设计或实现发生冲突时，先停止扩散，更新对应事实来源并取得产品确认。不得把 `.trae`、IDE 配置、临时聊天记录或个人笔记当作项目规范。

## 2. 开发环境

需要：

- Python 3.12 或更高的兼容版本。
- `uv`。
- Node.js 与 npm，版本需满足当前 Vite/TypeScript 的要求。
- Git。

后端依赖由 `uv.lock` 锁定：

```powershell
Copy-Item .env.example .env
uv sync --locked --extra dev
```

请立即修改 `.env` 中的 `APP_SECRET` 和管理员初始密码。`.env`、数据库、日志、构建目录和任何明文凭据都不能提交。

前端干净安装：

```powershell
Set-Location admin
npm ci
```

网络环境需要时可临时使用可信镜像，但不得把个人机器的全局代理、Token 或私有 Registry 凭据写入仓库。

## 3. 本地运行

启动后端：

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

另一个终端启动前端：

```powershell
Set-Location admin
npm run dev
```

生产式本地运行：

```powershell
Set-Location admin
npm run build
Set-Location ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

FastAPI 会托管已生成的 `admin/dist`。健康检查为 `GET /healthz`。

## 4. Git 工作流

本仓库由产品决定只使用 `master` 分支：

1. 修改前检查 `git status --short --branch` 和远端关系。
2. 保留用户已有且与当前任务无关的改动。
3. 不创建 feature、develop、release 或兼容分支。
4. 每个里程碑形成一个可运行、可验证的提交。
5. 只暂存本阶段文件，提交信息使用 Conventional Commits 前缀和中文说明。
6. 推送前确认本地 `master` 没有落后 `origin/master`。
7. 质量门禁通过后推送 `origin/master`。

示例：

```text
docs: 完成 M1 产品与架构规范
feat: 重建用户与 API Key 领域模型
fix: 修复任务名额重复释放
test: 增加三协议流式契约测试
```

禁止使用破坏性命令覆盖未提交改动，禁止把数据库、`.env`、日志、缓存、构建产物、二维码或演示 Key 纳入提交。

## 5. 实现顺序

每个功能按以下顺序推进：

1. 在 `docs/ROADMAP.md` 找到当前阶段和验收项。
2. 核对 `PRODUCT.md` 中的产品约束。
3. 核对或先更新 API、数据库和 UI 契约。
4. 先定义领域状态、错误和所有权，再实现 Service 和 Repository。
5. 接入 Router、Connector 或前端页面。
6. 增加正常、失败、权限和并发测试。
7. 运行完整质量门禁。
8. 实现、测试、文档和远端推送都完成后再勾选路线图。

不得为了让旧前端、旧数据库或旧测试继续工作而增加路由别名、字段别名、自动补列或双写。M2 必须在一个完整提交中同时切换 Schema、服务、API、前端和测试；其 A/B/C 工作包只跟踪进度，不能把新旧表或运行链路共存状态提交到 `master`。

## 6. 后端边界

目标依赖方向是：

```text
API -> Service -> Repository / Domain
```

- `app/api/`：HTTP/WS 边界、Pydantic Schema、鉴权和协议选择。
- `app/domain/`：不依赖框架的实体、值对象、枚举、状态机和领域错误。
- `app/services/`：用例编排、事务边界和外部能力协调。
- `app/repositories/`：SQLAlchemy 查询、持久化和原子条件更新。
- `app/connectors/`：可插拔 IM 平台适配。
- `app/protocols/`：OpenAI/Anthropic 解析、输出、SSE 和错误映射。
- `app/core/`：配置、数据库、加密、日志和应用生命周期。

Router 不写 SQL 或平台分支，Repository 不做网络调用，Domain 不依赖 FastAPI/SQLAlchemy/SDK。不要创建新的巨型 `api.py`、`services.py` 或职责不明的 `utils.py`。

Fake Model、LLM 配置和 API Key 权限必须严格区分：

- Fake Model 只是对外模型身份。
- LLM 配置是用户私有真实上游。
- API Key 决定所有者、策略、入口和有效 Fake Model 集合。
- 模型分组先预筛，Key 的直接模型选择再收窄；空选择代表全部候选模型。
- IM DSL、Web 编辑器、LLM 草稿和协议渲染器共享同一个 ReplyDraft；首个有效提交后没有撤销接口。
- `previous_response_id` 等网关控制字段必须按契约校验和等价展开，不能机械透传无法识别的内部 ID。

## 7. 前端边界

- 使用 React 19、TypeScript strict、Vite、React Router 和 Tailwind CSS 4。
- 不引入 Vue、Element Plus、CSS-in-JS 或第二套全量 UI 框架。
- 视觉和交互遵循 `docs/UI_GUIDE.md` 的浅色 RuoYi 风格。
- API 调用进入 `admin/src/api`，领域页面和组件进入对应 `features`。
- 通用页面框架、按钮、表格、Modal、Drawer、状态标签和分页进入 `components`。
- 菜单、路由、标题和能力使用单一配置来源。
- TypeScript 类型与后端契约同步，禁止用 `any` 掩盖不一致。
- Secret 只在一次性必要交互显示，不进入 localStorage、URL、console 或分析事件。

页面实际视觉由用户验收。开发者可以报告构建、自动化交互和截图结果，但不能把这些等同于用户最终验收。

## 8. 安全与数据规则

- 每个用户资源查询都显式包含所有权过滤；管理员能力也必须单独授权。
- 管理员不能替用户回复或取得用户 Secret。
- 禁用用户必须撤销会话和 Key、终止活动任务并幂等释放名额，不能只更新 `is_active`。
- 密码使用 Argon2id（`m=19456 KiB`、`t=2`、`p=1`，PHC 编码字符串）；邀请码和 API Key 只存哈希；LLM/IM Secret 按 DATABASE §2.4 的加密契约保存（文本 envelope + 按用途绑定 AAD）。
- Secret 明文永不通过 API 返回给管理员或其他用户；只有受信任的内部 Service 与 Connector Runtime 可为执行已授权动作临时解密。
- 日志、错误、审计和测试快照不能包含完整 Key、Authorization、Cookie、密码、Token、二维码或自定义 Header 值。
- 原始 LLM JSON 完整落库，但认证 Header 必须过滤。
- 同协议未知字段默认透传；跨协议实现必须逐项遵循 API 契约矩阵，不允许静默忽略、猜测转换或塞入 metadata。
- 调用方声明的 tool 绝不由系统执行。
- 邀请码次数、用户级 10 任务上限、首个回复和 fallback 必须依靠数据库原子裁决。

提交测试用 Secret 时只使用明显的虚构值，并确认它们不会被真实服务接受。

## 9. 测试策略

后端测试按领域组织，至少覆盖：

- 正常路径和输入边界。
- 普通用户之间的资源隔离。
- 管理员只读/治理边界和 Secret 不可见。
- 邀请码、任务名额、首个回复和 fallback 并发。
- 三种推理协议的 JSON、SSE 事件顺序、tool call 和错误格式。
- OpenAI Responses 历史引用、跨协议字段矩阵和不可转换字段 400。
- `/v1/models` 与实际 Fake Model 调用权限一致。
- 连接器消息幂等和单连接故障隔离。
- 日志和对外错误脱敏。
- 用户禁用、Key 删除、LLM 配置删除、连接重连和最后管理员保护。

前端至少验证 TypeScript 构建、关键权限分支、表单失败、空状态和窄屏布局。新增交互复杂页面时补充浏览器自动化测试；最终视觉仍交由用户确认。

测试不得连接真实微信、企微、用户 LLM 或生产数据库。外部 SDK 和 HTTP 使用可控替身。

## 10. 质量门禁

仓库根目录：

```powershell
uv lock --check
uv run --locked ruff format --check app tests
uv run --locked ruff check app tests
uv run --locked python -m pytest -q
uv build
git diff --check
```

前端目录：

```powershell
Set-Location admin
npm ci
npm run build
```

如果某项因外部环境无法执行，交付时明确报告命令、原因和未验证风险；不能用另一项成功代替。

## 11. 完成定义

里程碑可以在 `docs/ROADMAP.md` 标记“已完成”的前提：

- 所有验收项真正完成，不是占位或兼容代理。
- 实现与产品、API、数据库和 UI 文档一致。
- 权限、安全、失败恢复和并发路径有测试。
- 后端和前端质量门禁通过。
- README 和路线图同步。
- 工作树只包含预期变更，无 Secret 或无关生成物。
- 提交已经推送到 `origin/master`，远端提交可验证。

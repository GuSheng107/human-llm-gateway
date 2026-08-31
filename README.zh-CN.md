<div align="center">

<img src="docs/images/logo.svg" alt="Human LLM Gateway" width="120" height="120" />

# Human LLM Gateway

**对外是真 LLM API，对内由你（或你的真实 LLM）亲自回复。**

[![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](admin/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](pyproject.toml)
[![Tailwind](https://img.shields.io/badge/Tailwind%20CSS-4-06B6D4?logo=tailwindcss&logoColor=white)](admin/package.json)
[![Tests](https://img.shields.io/badge/tests-quality%20gates-brightgreen)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg)]()

[English](README.md) | **简体中文**

---

一次部署，把「你在 IM 里敲的字」变成「标准 LLM API 响应」。

</div>

---

## ✨ 这是什么

你是否想让某个工具调用「GPT-5」，但回复其实由**你自己**写？
或者你已经订阅了真实 LLM，想让它的输出**带上你自定义的身份**？

Human LLM Gateway 是一个可自托管的 **LLM 身份网关**：

```
调用方（SDK / 应用）                    你的网关                        回复来源
┌──────────────────┐   协议兼容请求   ┌──────────────────┐   ① 人工   ┌─────────────┐
│ openai SDK       │ ──────────────► │  Human LLM       │ ◄────────── │ 你，在 Web  │
│ anthropic SDK    │                 │  Gateway         │             │ 或 IM 里回复 │
│ 任何兼容客户端     │ ◄────────────── │                  │   ② LLM    ├─────────────┤
│                  │   Fake Model 响应 │                  │ ─────────► │ 你的真实    │
└──────────────────┘                 └──────────────────┘   ③ 混合    │ LLM 配置    │
                                                                      └─────────────┘
```

- **Fake Model** 只是对外身份（如 `gpt-5`）——**不绑定任何真实上游**
- **真实 LLM 配置**是你的私有上游——可以直转、可以生成草稿给你改、也可以在人工超时后兜底
- 调用方看到的响应里，`model` 永远是它请求的那个 Fake Model，永远不会暴露你的真实上游

## 🚀 核心特性

<table>
<tr><td width="50%" valign="top">

### 🎭 身份伪装
- Fake Model 目录：系统级 + 用户私有
- `/v1/models` 按 Key 计算有效集合
- 响应 `model` 字段永远改写为 Fake Model

### 📡 三协议兼容
- OpenAI Chat Completions
- OpenAI Responses（含 `previous_response_id` 链式展开）
- Anthropic Messages（`x-api-key` / `anthropic-version`）
- SSE 流式 + 伪流式输出

### 👤 人工回复闭环
- Web 任务工作台：完整原始请求 + 时间线 + 草稿
- IM 投递：微信 iLink / 企微 / Webhook / WebSocket / HTTP 轮询
- IM DSL：`::: reasoning` / `::: tool` 围栏，与 Web 编辑器共享结构
- 首个有效提交获胜，不可撤销

</td><td width="50%" valign="top">

### 🤖 真实 LLM 编排
- 用户级 LLM 配置（OpenAI 兼容 / Anthropic，密钥加密存储）
- 三种策略：`human` / `llm` 直转 / `human_fallback_llm` 超时兜底
- 跨协议字段矩阵：等价转换或明确 400，绝不静默丢弃
- 上游流式接收 → 完整落库 → 伪流式输出

### 🛡️ 安全纵深
- SSRF 分档防护（云元数据无条件拒 + 私有段开关）
- Secret 加密（HKDF + AES-GCM envelope），永不回显
- 页面上下文双层脱敏（封闭 schema + 正则擦洗）
- 请求体 8MiB / 1MiB 上限，流式字节/时长预算

### 🧰 工具沙箱
- 管理员白名单，用户显式确认执行
- 默认拒绝的 OCI 隔离：无网络、无挂载、只读根文件系统，并限制资源与输出
- 调用方 tool call 永不自动执行

</td></tr>
</table>

## 🏗️ 架构

```
                    ┌────────────────────────────────────────────┐
                    │                admin/ (React 19)           │
                    │   登录 · 工作台 · 任务 · 连接 · Key · 模型   │
                    │      LLM 配置 · 日志 · 工具 · 小助手         │
                    └────────────────────┬───────────────────────┘
                                         │ /api/*
┌──────────────┐  /v1/*  ┌───────────────▼────────────────┐  上游   ┌─────────────┐
│ 调用方 SDK     │ ──────► │  app/api/ (FastAPI)            │ ──────► │ 你的真实 LLM │
│ openai/anth.  │ ◄────── │  协议解析 · 准入 · 转发 · 渲染     │         │ (可选)       │
└──────────────┘  响应     │                                │         └─────────────┘
                           │  app/services/  用例编排          │  投递   ┌─────────────┐
┌──────────────┐  /conn.  │  app/repositories/  持久化        │ ──────► │ 你的 IM     │
│ 你的 IM 客户端 │ ──────► │  app/connectors/  IM 连接器       │  ◄────── │ 微信/企微/…  │
└──────────────┘  回复DSL  │  app/protocols/  三协议适配        │   回复   └─────────────┘
                           │  app/domain/  枚举/状态机/纯规则    │
                           │  app/core/  安全/配置/日志          │
                           └────────────────────────────────┘
```

**技术栈**：Python 3.12 · FastAPI · SQLAlchemy · Pydantic v2 · Argon2id · AES-256-GCM · React 19 · TypeScript strict · Vite · Tailwind CSS 4 · SSE

## 📦 快速开始

### 环境要求

- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 18+
- 获批工具需要沙箱执行时安装 Docker 或 Podman

### 三步启动

```bash
# 1. 克隆
git clone https://github.com/GuSheng107/human-llm-gateway.git
cd human-llm-gateway

# 2. 生成密钥并配置
python -c "import secrets; print(f'APP_SECRET={secrets.token_urlsafe(32)}')" >> .env
echo "ADMIN_USERNAME=admin" >> .env
echo "ADMIN_PASSWORD=Your-Str0ng!Pass" >> .env

# 3. 构建前端静态资源，然后启动后端（后端直接托管 SPA，单端口访问）
uv sync --locked
(cd admin && npm ci && npm run build)
uv run uvicorn app.api:app --host 0.0.0.0 --port 8000
```

打开 **http://127.0.0.1:8000** — 管理台与 API 同端口。首次启动自动建库并写入默认系统模型，用 `.env` 中的管理员登录，改密后即可签发邀请码、创建用户。

> 前端开发热更新（可选）：`cd admin && npm run dev` → http://127.0.0.1:5173（`/api`、`/v1` 自动代理到 8000）

### 五分钟体验

```bash
# ① 管理台创建 API Key（选一个 Fake Model，比如 deepseek-v4-pro）

# ② 像调用 OpenAI 一样调用它
export OPENAI_API_KEY="sk-xxxx"    # 网关签发的 Key
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"

python -c "
from openai import OpenAI
client = OpenAI()
stream = client.chat.completions.create(
    model='deepseek-v4-pro',          # Fake Model
    messages=[{'role': 'user', 'content': '你好'}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or '', end='')
"
# ③ 同时打开 Web 任务工作台——任务正在等你人工回复
# ④ 回复提交后，调用方收到伪流式输出
```

## 🗺️ 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0–M1 | 结构收口 · 产品/架构/API/数据库/UI 规范 | ✅ |
| M2 | 领域模型与数据库原子重建（20 张目标表） | ✅ |
| M3 | 用户 · 邀请码 · 权限闭环 | ✅ |
| M4 | IM 连接与任务投递（5 平台连接器） | ✅ |
| M5 | Fake Model 目录 · 分组 · API Key · 并发准入 | ✅ |
| M6 | 三协议契约 · 任务工作台 · 人工提交闭环 | ✅ |
| M7 | LLM 配置 · 草稿生成 · 自动转发 · 跨协议矩阵 · 流式 | ✅ |
| M8 | 全局 Web 小助手（上下文脱敏） | ✅ |
| M9 | 控制台统计 · 日志审计 · 体验收口 | ✅ |
| M10 | 部署运维（Docker/CI/readyz/metrics/备份） | ⏳ |
| M11 | 发布验收 | ⏳ |
| M12 | 隔离工具沙箱 | ✅ |

完整计划见 [ROADMAP](docs/ROADMAP.md)。当前测试数量以末尾质量门禁的实际输出为准。

M12 在 Windows、macOS 与 Linux 上统一使用默认拒绝的 Docker/Podman OCI 沙箱。
默认镜像构建方式与安全边界见 [SANDBOX](docs/SANDBOX.md)。

## 🤝 参与贡献

```bash
# 质量门禁（提交前必须全绿）
uv lock --check
uv run --locked ruff format --check app tests
uv run --locked ruff check app tests
uv run --locked python -m pytest -q
cd admin && npm ci && npm run build && npm test
```

开发规范见 [AGENTS.md](AGENTS.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 📄 许可证

[AGPL-3.0](LICENSE) © Human LLM Gateway Contributors

> 任何修改版通过网络提供服务时，必须向远程交互用户提供获取对应源码的机会。

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=GuSheng107/human-llm-gateway&type=Date)](https://star-history.com/#GuSheng107/human-llm-gateway&Date)

---

<div align="center">

**如果这个项目对你有帮助，请点一个 Star ⭐**

[报告问题](https://github.com/GuSheng107/human-llm-gateway/issues) · [功能讨论](https://github.com/GuSheng107/human-llm-gateway/discussions)

</div>

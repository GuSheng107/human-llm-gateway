# 管理台 UI/UX 需求文档 V2

> 本文档基于 master 分支（最近 7 次提交：b6f5d1e / 667b416 / 16f2cb8 / b318885 / c1bf0d1 / 4df8e0e / 1c7c44d）当前代码状态梳理，标注每条需求的关键文件位置与现状差距。
>
> 本文档**不涉及代码改动**，仅作为下一阶段实施的需求输入。

---

## 0. 全局约定（适用全部需求）

| 项 | 约定 |
| --- | --- |
| 目标版本 | 一次大版本迭代，统一发布（建议打包为 0.7.0） |
| 数据库兼容 | **不破坏现有表 schema**；新增字段允许 nullable；新增表允许 create_all；不允许删除字段。涉及后端 `app/core/constants.py` 的 `SCHEMA_VERSION` 暂不 bump（旧库可继续） |
| 协议兼容 | OpenAI Chat / OpenAI Responses / Anthropic Messages 三种客户端协议在 API 入口已实现（`app/protocols/`），本次需求中协议选择器必须覆盖这三种 |
| 角色区分 | 管理员（`admin`）/ 普通用户（`user`）。`FakeModel` 范围字段（system / private）继续保留 |
| 错误展示 | 沿用现有 `ErrorBanner / Toast / Modal` 组件，**禁止引入新组件库** |
| 图标 | 沿用 `admin/src/icons.tsx` 自绘 SVG（现有 23 个），新增图标统一在此文件追加，不引入图标库 |
| 样式 | 沿用 Tailwind v4 + `field-input` 实用类 |
| 多语言 | 暂不引入 i18n；中文字符串直接硬编码 |
| 开发模式 | Vite dev server 5174 端口（5173 被占），后端 uvicorn 8001 端口（8000 被 CLodop 占用） |

---

## 1. 认证流程优化

### 1.1 强制改密页不再要求"原密码"

**现状：**
- 前端 [ForcePasswordPage.tsx:14, 36, 61-68](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/ForcePasswordPage.tsx#L14) 有 `currentPassword` 字段。
- 前端调用 `changePassword(currentPassword, newPassword)`，实际是普通 `/api/account/password` 端点。
- 后端 [app/api/account.py:25-65](file:///d:/pyPrj/human-llm-gateway/app/api/account.py#L25) 的 `PasswordChange.current_password: Field(min_length=1)` **强制非空**。
- `UserService.change_password`（[app/services/user_service.py:245-271](file:///d:/pyPrj/human-llm-gateway/app/services/user_service.py#L245)）也强制 `verify_password(current_password, user.password_hash)`。
- 而 `must_change_password=True` 的用户已经在登录时被拦截（[App.tsx:44, 66, 73](file:///d:/pyPrj/human-llm-gateway/admin/src/App.tsx#L44) 强制跳到 `/change-password`）。

**需求：**
- 强制改密流程（`must_change_password=True`）**不再校验旧密码**。此时用户已经用临时密码登录，前端可证明自己持有当前会话。
- 普通"账号设置 → 修改密码"流程**保留旧密码校验**。

**实现路径（仅描述，不改）：**

后端拆分 `change_password`：
- 新增 `POST /api/account/password/forced` 端点（或在原端点用 `?forced=true` 标识），签名 `PasswordForcedChange(new_password)`。
- `require_full_session`（已登录且 `must_change_password=True`）才允许访问；进入端点后跳过旧密码校验，调用 `UserService.set_password` 写新 hash 并将 `must_change_password` 置 false、撤销全部其它会话（与 `reset_password` 行为一致）。
- 原 `POST /api/account/password` 端点**保留**，行为不变。

前端：
- `ForcePasswordPage` 删除 `currentPassword` 字段及 UI，提交时调用新端点。
- `AccountPage` 的"修改密码"区块**保留当前密码字段**不变。

**验收：**
- 用户以临时密码登录 → 跳 `/change-password` → 只需填新密码两次 → 提交后直接进 `/console`，提示改密成功。
- 管理员在"用户管理"重置某用户密码后，该用户再登录改密流程同上。

### 1.2 注册成功后登录页预填刚注册的账号

**现状：**
- [RegisterPage.tsx:55-65](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/RegisterPage.tsx#L55) 注册成功后 `navigate("/login", { replace: true })`，登录页无任何途径知道刚才注册的账号。
- [LoginPage.tsx:43](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/LoginPage.tsx#L43) 的 `username` 初值只读 localStorage `hlg_remembered_username`。
- 注意：现有 localStorage 设计会把 `username` 一直留着；新需求不是"清除"，而是"注册时把用户名也写进这个 key"。

**需求：**
- 注册成功 → 跳转登录 → 登录页 `username` 输入框已预填刚注册的用户名；用户只需输入密码 + 验证码。
- 密码框不回显（仍是空的）。
- 预填的账号也写一次 localStorage（`hlg_remembered_username`），与"记住用户名"的语义一致。

**实现路径：**
- `RegisterPage` 提交成功后 `localStorage.setItem("hlg_remembered_username", form.username.trim())`，再 `navigate("/login")`。
- `LoginPage` 不需改动（它已读 localStorage）。
- 现有 `rememberPassword` 复选框**不动**（语义是"下次也记住账号"，与新需求一致）。

**验收：**
- 在 `/register` 填完表单 → 注册成功 → 跳转到 `/login` → 账号框已显示刚填的 username。
- 之后刷新登录页，账号仍保留（与现状一致）。

---

## 2. 界面文案清理（第一层筛选）

**现状：** [App.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/App.tsx) / 各页面没有"筛选"文案；在 [RegisterPage.tsx:103](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/RegisterPage.tsx#L103) 等地方有少量"可选/选填/必填"标记。需要你提供具体要删除的文案截图或位置。

**需求：**
- 标记为「可选，第一层筛选」说明用户希望"把界面中冗余的提示/筛选/介绍性文案清理到最低"——例如：
  - 卡片描述（`description` 字段）只在第一次访问时显示，确认后用户可手动关闭并写入 localStorage。
  - 字段的 hint 文字（`选填，用于找回与通知`）只在 placeholder 体现，不在 label 下重复说明。
  - 表格列头下的脚注（`共 N 个`）移入分页区。
- 业务上**不删任何功能**，只调整展示密度与位置。

**实现路径：**
- 引入一个轻量"DismissCard"组件：接受 `id` 和 `children`，检查 `localStorage["hlg_dismissed_<id>"]`，命中则不渲染；用户点关闭后写入。
- [models / llm-configs / connections / tasks](file:///d:/pyPrj/human-llm-gateway/admin/src/features) 等页面的 `PageHeader description` 改为"首次显示 + 可关闭"模式。
- 字段 hint（`hint` prop）按"重复 placeholder/label 即删除 hint"原则过一遍。

**验收：**
- 关键页面（控制台、模型目录、LLM 管理、连接 IM、任务记录）首次进入有简介卡片；点击关闭后再不出现。
- 字段 hint 数量减少到当前 30% 以下（具体数字按截图统计后定）。

---

## 3. 模型目录页改造：参考 newapi 模型广场

**现状：**
- [ModelsPage.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/models/ModelsPage.tsx) 是"系统/私有" + 简单列表 + 启停 + 分组管理。
- `FakeModel` 类型 [types/gateway.ts:77-88](file:///d:/pyPrj/human-llm-gateway/admin/src/types/gateway.ts#L77) 仅 `id/owner/model_id/display_name/owned_by/description/sort_order/is_enabled/created_at`。
- 后端 `app/repositories/models/catalog.py` + `app/services/fake_model_service.py` 也只对应这些字段。

**参考：** newapi 模型广场 — 左侧"供应商分组 + 计费类型 + 标签"多层级筛选、右侧"模型卡片网格"展示（图标 + 模型名 + 输入价/补全价 + 缓存价 + 能力徽标 + 复制/对比按钮）。

**需求：**

### 3.1 数据模型扩展（FakeModel 能力字段）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `input_price_per_million` | `Decimal` (NUMERIC(20,6)) | NULL | 输入价 元/1M tokens |
| `output_price_per_million` | `Decimal` | NULL | 输出价 |
| `cached_input_price_per_million` | `Decimal` | NULL | 缓存读入价（部分模型支持） |
| `cached_write_price_per_million` | `Decimal` | NULL | 缓存写价 |
| `context_window` | `Integer` | NULL | 上下文窗口 token 数 |
| `max_output_tokens` | `Integer` | NULL | 最大输出 token |
| `capabilities` | `JSON` (list[str]) | `[]` | 能力标签，enum: `vision` / `tools` / `thinking` / `image_gen` / `audio` / `video` / `streaming` / `function_calling` |
| `billing_tier` | `Enum` | `pay_as_you_go` | `pay_as_you_go` / `subscription` / `free` / `dynamic` |
| `endpoint_type` | `Enum` | `openai_chat` | `openai_chat` / `openai_responses` / `anthropic_messages` |
| `logo_url` | `String(512)` | NULL | 厂商 logo（外链或 base64） |
| `tags` | `JSON` (list[str]) | `[]` | 自定义标签（如 `代码`、`数学`、`多模态`） |

**数据库：** `fake_models` 表 ALTER TABLE 新增列，全部 nullable。`app/repositories/models/catalog.py` 模型同步增加字段。`app/services/bootstrap.py` 已有 `seed_default_system_models`（[bootstrap.py:93-96](file:///d:/pyPrj/human-llm-gateway/app/services/bootstrap.py#L93)），将默认模型补齐上述字段。

### 3.2 前端页面改造

- 页面标题改为"模型广场"。
- 顶部 Banner：展示供应商总数、可用 token 价范围、缓存价模型数量（参考 newapi 紫色 banner 形态）。
- 左侧侧栏（约 200px 固定宽度）：
  - 供应商：全部 / 启用模型中出现的所有 `owned_by`（从后端聚合）。
  - 可用令牌分组：列出 `ModelGroup`（沿用现有"模型分组"概念，作为快速筛选 chip）。
  - 计费类型：全部 / 按量计费 / 订阅 / 免费 / 动态计费。
  - 标签：全部 / 选中模型中出现的所有 tag。
  - 端点类型：全部 / openai / anthropic。
  - **删除旧的"未启用"等弱文案**（需求 2 一并处理）。
- 右侧主区：
  - 顶部搜索框（模糊匹配 `model_id/display_name/description/tags`）。
  - 右侧控件：复制（复制 model_id）、"倍率显示"开关、"表格视图 / 网格视图"切换、视图密度切换（M 标识 = Minimal）。
  - 模型卡片网格（每行 3 张卡片，桌面端）：
    - 左上：厂商 logo（48×48），无 logo 时回退首字母。
    - 模型 ID（mono 字体，主色加粗）。
    - 能力徽标行：vision / tools / thinking / image 等小徽标。
    - 价格列表：输入 / 补全 / 缓存读 / 缓存写（存在才显示，缺失则不展示该行，不显示"-"）。
    - 右上角：复制按钮 + 对比按钮（加入对比抽屉，最多 4 个）。
    - 底部：计费类型 chip。
- 表格视图：列固定为 `model_id / 显示名 / 端点 / 上下文 / 输入价 / 输出价 / 缓存读 / 缓存写 / 能力 / 标签 / 状态 / 操作`，沿用现有 `StatusBadge`。
- 管理动作：每张卡片右下"⋯"菜单包含：启停、编辑（沿用现有 Modal 形态，扩展字段）、删除。

### 3.3 编辑/创建模型

- 现有 [ModelsPage.tsx:260-306](file:///d:/pyPrj/human-llm-gateway/admin/src/features/models/ModelsPage.tsx#L260) 的"新建 Fake Model" Modal 升级为多 Tab：
  - **基本**：model_id、display_name、logo_url、scope（仅管理员可选 system；普通用户固定 private）。
  - **能力**：capabilities 多选、endpoint_type 单选、context_window、max_output_tokens。
  - **价格**：输入/输出/缓存读/缓存写 4 个数字输入，0 表示免费，null（留空）表示不展示该行。
  - **计费**：billing_tier 单选。
  - **标签**：tags 自由输入，Enter 添加 chip，X 删除。
- 所有新字段都需要前后端 Pydantic schema 同步（参考 newapi 的 `UpdateOtherModel` / `CreateOtherModel`）。

### 3.4 后端配套

- `app/services/fake_model_service.py`：新建/更新/列表的入参接受以上字段；列表接口支持 `?provider=&billing_tier=&endpoint_type=&capability=&tag=&search=` 多维筛选 + 分页。
- `app/api/fake_models.py`：路由同步改造，开放 `?include_disabled=true` 控制是否包含停用模型（默认仅启用）。
- **不改动 `v1/models` 协议响应字段**——继续只暴露 OpenAI 标准的 `id / owned_by / ...`，新字段不进协议（仅管理台用）。

### 3.5 验收

- 默认 system 模型的 5 个左右样本（deepseek-v4-pro / gpt-5.6 / claude-3 等）能完整展示 logo、价格、缓存、能力。
- 左侧筛选 chip 与右侧网格联动正确，URL 同步查询参数（便于分享）。
- 网格/表格切换状态写入 localStorage，刷新保留。

---

## 4. LLM 管理：补齐参数配置 + 协议选择

**现状：**
- 表单 [LlmConfigsPage.tsx:347-516](file:///d:/pyPrj/human-llm-gateway/admin/src/features/llm/LlmConfigsPage.tsx#L347) 只有：name / protocol(二选一) / base_url / api_key / model / timeout / 自定义 header / enabled。
- `LlmProtocol` [api/llmConfigs.ts:4](file:///d:/pyPrj/human-llm-gateway/admin/src/api/llmConfigs.ts#L4) = `"openai_compatible" | "anthropic"`，**没有 `openai_responses`**。
- 后端 `app/repositories/models/llm.py` + `app/services/llm_config_service.py` 也只支持上述两协议。
- 协议适配层 `app/protocols/` 已实现 OpenAI Chat / OpenAI Responses / Anthropic Messages 三种客户端入参解析（[app/protocols/cross.py](file:///d:/pyPrj/human-llm-gateway/app/protocols/cross.py)）——但**转发到上游的协议映射**目前由 `chat_completions.py / responses.py / anthropic.py` 决定，尚未由 LLM Config 控制目标协议。

**参考图二（newapi 编辑模型）：** 协议下拉、完整 URL、模型 ID、显示名、API 密钥、模型系列（删除）、上下文窗口（输入/输出 token，常用预设 128k/256k/512k/1M）、工具调用轮数、是否支持图片输入、思考模式三档（跟随模型/开启/关闭）、采样参数（Temperature / Top P / Top K）。

**需求：**

### 4.1 数据模型扩展（llm_configs 表）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `protocol` | `Enum` | `openai_chat` | **改为枚举**：`openai_chat` / `openai_responses` / `anthropic_messages`（值变化需 migration） |
| `default_temperature` | `Decimal(3,2)` | NULL | 默认采样温度（0.00–2.00） |
| `default_top_p` | `Decimal(3,2)` | NULL | 默认 Top-P（0.00–1.00） |
| `default_top_k` | `Integer` | NULL | 默认 Top-K（1–100） |
| `max_output_tokens` | `Integer` | NULL | 单请求最大输出 token |
| `context_window_input` | `Integer` | NULL | 输入上下文窗口 |
| `context_window_output` | `Integer` | NULL | 输出上下文窗口 |
| `max_tool_call_rounds` | `Integer` | 16 | 工具调用最大轮数（与需求 10 配合） |
| `supports_image_input` | `Boolean` | `false` | 是否支持图片输入（影响 admission 校验） |
| `thinking_mode` | `Enum` | `model_default` | `model_default` / `enabled` / `disabled` |
| `thinking_level` | `Enum` | NULL | `low` / `medium` / `high`（**仅 OpenAI Responses** 支持；其他协议 NULL 或 model_default） |
| `extra_body` | `JSON` | `{}` | 透传 `extra_body` 给上游（深度的厂商参数；如 glm/通义专有参数） |

**migration 策略：**
- 旧库的 `protocol` 字段为字符串 `openai_compatible` / `anthropic`，**ALTER 旧值到新值**：`openai_compatible` → `openai_chat`，`anthropic` → `anthropic_messages`。
- 新增列 nullable。
- **不 bump SCHEMA_VERSION**（需求 0 约定），但写入 migration SQL 在 `app/services/bootstrap.py` 启动时执行（用 `_migrate_post_load` 风格，仅补齐字段，不动数据）。

### 4.2 后端 LLM Config 服务

`LlmConfigPayload` / `LlmConfigUpdatePayload`（[api/llmConfigs.ts:11-31](file:///d:/pyPrj/human-llm-gateway/admin/src/api/llmConfigs.ts#L11)）同步增加新字段。

`app/services/llm_config_service.py` 校验：
- `thinking_level` 仅在 `protocol == "openai_responses"` 时可设置；其他协议忽略或抛 400。
- `supports_image_input=true` 的 LLM Config 才能在 admission 接受包含 `image_url` 内容的请求。

`app/services/llm_forward_service.py`（[app/services/llm_forward_service.py](file:///d:/pyPrj/human-llm-gateway/app/services/llm_forward_service.py)）：
- 转发前按 `protocol` 决定走 `chat_completions.py` / `responses.py` / `anthropic.py` 的具体编码。
- OpenAI Responses 协议额外应用 `thinking_level`（`reasoning.effort` 字段），与 OpenAI 官方一致。
- 应用 `default_*` 与 `extra_body`，与请求体合并（请求体显式提供的字段优先）。

`llm_upstream.py` 的连接测试（`testLlmConfig`）一并按新协议字段验证连通性。

### 4.3 前端表单（参考 newapi）

按 [LlmConfigsPage.tsx:347-516](file:///d:/pyPrj/human-llm-gateway/admin/src/features/llm/LlmConfigsPage.tsx#L347) 现状大改：

- **API 格式**（协议）：下拉选 `OpenAI Chat Completions 格式` / `OpenAI Responses 格式` / `Anthropic 格式`。三者文字按需。
- **完整 URL 开关**：默认 base_url 截断到 host，开启后显示完整 URL（含路径）。
- **Base URL**：根据协议自动补全占位提示（`https://api.openai.com/v1` / `https://api.anthropic.com/v1`）。
- **模型 ID** / **模型显示名**（沿用）。
- **API 密钥**：留空保留旧值（沿用）。
- **高级配置**（折叠区，按 newapi 视觉）：
  - **上下文窗口（Token）**：输入 + 输出 两行输入框，右下快捷 chip `128k / 256k / 512k / 1M`。
  - **工具调用轮数**：数字输入，默认 500（newapi 风格）。
  - **支持图片输入**：单选 `支持 / 不支持`。
  - **思考模式**：单选 `跟随模型默认配置 / 开启 / 关闭`；当协议 = OpenAI Responses 时额外显示**思考等级**下拉 `low / medium / high`。
  - **采样参数**：Temperature / Top P / Top K 三个数字输入（带范围提示），留空表示跟随上游默认。
  - **Extra Body**：JSON 编辑器（折叠），带 schema 校验。
- **连通性测试**：沿用，弹窗展示结果。
- **删除"模型系列"字段**（用户明确要求）。

### 4.4 验收

- 创建一条"OpenAI Responses 协议 + 思考等级 high"的 LLM Config，转发一条 chat 请求，下游日志能看到 `reasoning.effort=high`。
- Anthropic 协议下隐藏"思考等级"控件。
- Top P 输入 1.5 立即校验失败（前端 + 后端）。
- 数据库新增列可见，老数据 `protocol` 字段被自动改写。

---

## 5. IM 管理：参考 Hermes 前端交互 + 微信扫码

**现状：**
- [ConnectionsPage.tsx:104-202](file:///d:/pyPrj/human-llm-gateway/admin/src/features/connections/ConnectionsPage.tsx#L104) 是简单表格 + 操作列。
- 微信 iLink 连接器（[wecom_ilink.py:153-185](file:///d:/pyPrj/human-llm-gateway/app/connectors/implementations/wecom_ilink.py#L153)）后端**已有 `start_login / poll_login`**：
  - `start_login` 返回 `{ qrcode, qrcode_img_content }` —— `qrcode_img_content` 是 bytes（PNG）。
  - `poll_login` 轮询扫码状态，**confirmed** 时返回 `bot_token`（用户保存到 config）。
- 前端 [ConnectionsPage.tsx:268-294](file:///d:/pyPrj/human-llm-gateway/admin/src/features/connections/ConnectionsPage.tsx#L268) 只用了 `binding_code` 弹窗（旧流程：IM 端发绑定码给机器人），**完全没用 `start_login` 走扫码**。
- 其他平台（wecom_aibot、wecom_ilink、http_poll、webhook、ws_server）后端通过 [app/connectors/registry.py](file:///d:/pyPrj/human-llm-gateway/app/connectors/registry.py) 注册，前端通过 `/api/im-platforms` 拿到 schema。

**参考 Hermes：** 主从结构 + Tab 区分"账号"与"机器人"；微信/企业微信用扫码登录抽屉，配二维码 + "请用微信扫一扫" + 倒计时 + 状态文字（"等待扫码" → "已扫码，请在手机上确认" → "登录成功" → "已过期，请重新扫码"）。抽屉内含"使用其他方式登录"链接（绑定码流程作回退）。

**需求：**

### 5.1 连接列表页（保留现状 + 强化视觉）

- 列表项改为卡片式（每行一张），左 1/3 放"平台 logo + 名称 + 状态"；右 2/3 放"健康指标 / 错误 / 所有者"。
- 卡片右上角"⋯"菜单：启动/停止 / 应用 / 编辑 / 检查健康 / 绑定(企业微信) / 删除。
- 搜索框保留，沿用 [ConnectionsPage.tsx:138-149](file:///d:/pyPrj/human-llm-gateway/admin/src/features/connections/ConnectionsPage.tsx#L138)。

### 5.2 创建/编辑连接 Modal（沿用现状，补强 schema 校验）

- 沿用 [ConnectionFormModal.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/connections/ConnectionFormModal.tsx) 的动态表单（按 `PlatformField` 生成）。
- 新增：字段下方按 `field.type` 与 `field.description` 给出 inline 提示（淡化灰）；secret 字段不显示已保存值，提供"重置"按钮。

### 5.3 **新增：扫码登录抽屉**（核心需求）

适用范围：平台 `kind === "client"` 且 `supports_login === true`（当前只有 `wecom_ilink`）。

UI：
- 抽屉宽 480px，右侧滑入。
- 顶部：平台名 + 关闭按钮。
- 二维码区域：正方形 240×240 居中，圆角 8px，白底，深色边框。
- 状态文字：随状态变化文案
  - `init` → "请用微信扫一扫二维码"
  - `scanned` → "已扫码，请在手机上点击确认"
  - `confirmed` → "登录成功，正在保存凭据…" → 自动关闭并刷新列表
  - `expired` → "二维码已过期" + "刷新二维码"按钮
  - `error` → "出错：xxx" + "重试"按钮
- 倒计时：从 `start_login` 后开始（5 分钟），倒计时归零切到 `expired`。
- 底部链接："使用其他方式登录（绑定码）" → 切换到 [ConnectionsPage.tsx:268-294](file:///d:/pyPrj/human-llm-gateway/admin/src/features/connections/ConnectionsPage.tsx#L268) 现有的 `binding_code` Modal。
- 关闭抽屉时取消轮询（AbortController），避免泄漏。

前端调用：
- `POST /api/im-connections/{id}/login` → 拿 `{ qrcode, qrcode_img_content }`；`qrcode_img_content` 是 bytes，前端用 `URL.createObjectURL(new Blob([bytes], { type: "image/png" }))` 或 `data:image/png;base64,...` 显示。
- `GET /api/im-connections/{id}/login`（每 2s 轮询一次）→ `{ status, bot_token? }`；`status === "confirmed"` 时拿到 `bot_token` 自动 PATCH 到连接 config（前端直接 PATCH 调 `updateConnection` 写入 `config.bot_token`），后端按 `start_login → poll_login → 写入 bot_token` 已具备。

**新增后端 helper（仅供前端展示 qrcode，不改后端核心逻辑）：**
- 可选：在 `ConnectionView` 增加 `supports_login: bool` 字段透传，**或**前端通过 `/api/im-platforms` 已有的 `supports_login` 自行判断（推荐后者，避免 schema 变更）。

### 5.4 验收

- 点击"连接 IM"新建 `wecom_ilink` 连接 → 输入名称 + 平台 + token → 保存 → 列表里看到这条 → 点击"扫码" → 抽屉滑出显示二维码。
- 实际用微信扫描 → 抽屉内状态文字依次变化 → 确认后抽屉自动关闭，列表里该连接 `state` 变为 `bound` / `running`。
- 倒计时归零后显示"二维码已过期"，点"刷新"重新获取。
- 关闭抽屉立即停止轮询（DevTools Network 验证）。
- 不支持扫码的平台（`http_poll` / `webhook` / `wecom_aibot` / `ws_server`）不显示"扫码"按钮。

---

## 6. 增大头像大小限制

**现状：**
- 前端 [AccountPage.tsx:14](file:///d:/pyPrj/human-llm-gateway/admin/src/features/settings/AccountPage.tsx#L14) `MAX_AVATAR_BYTES = 256 * 1024`（256KB 原图）。
- 前端 [AccountPage.tsx:22-35](file:///d:/pyPrj/human-llm-gateway/admin/src/features/settings/AccountPage.tsx#L22) canvas 输出固定 160×160 PNG。160×160 PNG base64 通常 30–60KB。
- 后端 [app/api/account.py:22](file:///d:/pyPrj/human-llm-gateway/app/api/account.py#L22) `ProfileUpdate.avatar_base64: Field(max_length=400_000)`（~292KB base64）。
- `User.avatar_base64` 在 `app/repositories/models/auth.py` 字段类型需要确认（推测 Text / LongText）。

**需求：**
- 前端原图上限提升到 **2 MB**。
- canvas 输出尺寸提升到 **320×320** PNG（适应 2× 屏 + 头像组件在不同位置可能放大）。
- 头像是 base64 存储，**2 MB 原图 + 320×320 PNG 输出后 base64 约 80–150KB**；后端 max_length 提升到 **2_000_000**（留足 10× 冗余）。
- DB `avatar_base64` 字段若为 VARCHAR 需 ALTER 为 TEXT；若已为 TEXT 不动。
- 压缩质量策略：若输出 PNG 仍 > 1MB，则降级为 200×200 再输出；若仍 > 1MB 报错"处理失败"。

**验收：**
- 上传 2MB 原图 → 看到头像正确保存。
- 上传 5MB 原图 → 提示"头像原图需小于 2MB"。
- 个人资料页、保存资料、登录后 user 响应中的 `avatar_base64` 与上传一致。
- 列表/抽屉/任务回复等处的头像按 320×320 显示（不重新处理）。

---

## 7. 浏览器导航栏 icon 与应用 icon 一致

**现状：**
- [admin/index.html:7](file:///d:/pyPrj/human-llm-gateway/admin/index.html#L7) 引用 `/favicon.svg`。
- `admin/public/favicon.svg` 存在（与 `docs/images/logo.svg` 是否同源需核对）。
- 登录页 [LoginPage.tsx:138-140](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/LoginPage.tsx#L138) 顶部使用 `<Icon name="gateway" />`。
- 侧栏 logo、登录页 logo、用户头像（首字母回退）三处视觉不统一。
- `<meta name="theme-color" content="#0e1417" />` 浏览器顶栏色固定深色。

**需求：**

### 7.1 统一 favicon 与 logo

- 选定**主 logo**（建议用 `docs/images/logo.svg` 作为源文件；若 `admin/public/favicon.svg` 风格不一致，**以 logo.svg 为准，覆盖 favicon.svg**）。
- `admin/index.html` 保持引用 `/favicon.svg`（Vite dev 与生产 dist 都正确解析）。
- 生产环境下后端 [app/api/__init__.py:95](file:///d:/pyPrj/human-llm-gateway/app/api/__init__.py#L95) 的 `StaticFiles(directory=admin_dist)` 会暴露 `favicon.svg`（如果它在 `admin/public/`，build 后会复制到 `admin/dist/favicon.svg`）。验证 Vite 默认 `publicDir=public`，确认。
- iOS / Android 设备图标（可选 PWA 化）：补充 `apple-touch-icon.png`（180×180）和 `mask-icon.svg`，写在 index.html。

### 7.2 应用内 logo 统一

- 引入 `admin/src/components/brand/Brand.tsx` 组件：接受 `size: "sm" | "md" | "lg"` 与 `withText: boolean`；内部渲染统一 SVG（**从 `admin/public/favicon.svg` 内嵌** 或用 React 组件化的版本，保证与浏览器一致）。
- 替换位置：
  - 登录页 [LoginPage.tsx:138-140, 226-228](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/LoginPage.tsx#L138) — 替换为 `<Brand size="md" withText />`。
  - 注册页 [RegisterPage.tsx:88-90](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/RegisterPage.tsx#L88) — 同上。
  - 顶栏 AppShell — 新增品牌位（沿用 [components/layout/AppShell.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/components/layout/AppShell.tsx) 的 header 区）。
  - 强制改密页 [ForcePasswordPage.tsx:54-56](file:///d:/pyPrj/human-llm-gateway/admin/src/features/auth/ForcePasswordPage.tsx#L54) — 改为通用 Brand 组件（去"!"字图标）。

### 7.3 验证

- 浏览器标签页 favicon 渲染与登录页 Brand 视觉一致。
- 深色顶栏色保留 `theme-color`，与品牌主色（`docs/images/logo.svg` 提取 hex）一致；更新 `index.html` 的 `meta theme-color` 与品牌色一致（例：主蓝 `#1d4ed8`）。
- PWA 化（可选）：新增 `manifest.webmanifest` 引用 logo.png 192/512。

---

## 8. 网页小助手：参考阿里云 AI 助理等成熟方案

**现状：**
- [AssistantPanel.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/AssistantPanel.tsx) 已有：浮动按钮 → 抽屉 → 会话列表 / LLM 配置下拉 / 消息列表 / 输入框。
- [AssistantContext.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/AssistantContext.tsx) 已支持会话管理。
- [bridge.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/bridge.ts) 已实现与任务回复编辑器的跨 feature 通信。
- [contextRegistry.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/contextRegistry.ts) 已在各 feature 注册上下文资源（白名单）。
- [api/assistant.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/api/assistant.ts) 提供 5 个端点（CRUD session + send message）。
- 后端 [app/services/assistant/service.py](file:///d:/pyPrj/human-llm-gateway/app/services/assistant/service.py) + [app/services/assistant/redaction.py](file:///d:/pyPrj/human-llm-gateway/app/services/assistant/redaction.py) 已实现。

**参考阿里云 AI 助理**（用户提供的截图）：右下浮动按钮 → 抽屉 → 顶部"+" / 历史 / 全屏 / 收起 / 关闭；中部"你好，我是 XXX"欢迎语 + 两个示例卡（了解产品 / 方案推荐）；底部输入框（Shift+Enter 换行）；侧边有"消息/语音/小工具"快捷入口（浮动）。

**需求：**

### 8.1 UI 优化

- **浮动按钮**：保留 [AssistantPanel.tsx:154-163](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/AssistantPanel.tsx#L154) 圆形 FAB，**改为紫色渐变**（与品牌色一致），加 hover 状态。
- **抽屉宽度**：480px → 改为 420px（更紧凑）。
- **顶部工具栏**（沿用并增强）：
  - 左：助手图标 + "AI 助手" + 当前会话标题（点击可重命名）。
  - 中：`+` 新建会话（点击直接调用 `createAssistantSession`，使用上次选择的 LLM）。
  - 右：历史记录抽屉（嵌套抽屉）、全屏模式（占满右侧 z-50）、收起、关闭。
- **欢迎区**（无消息时显示，参考阿里云风格）：
  - 标题"你好，我是 AI 助手"。
  - 副标题"能工智人网关的智能陪伴，可基于当前页面与任务草稿回答"。
  - 3 张示例卡："解释当前页面的 LLM 配置" / "起草任务回复" / "总结最近的连接错误"，点击自动填入输入框。
- **消息区**：
  - 用户消息右对齐，蓝色气泡。
  - AI 消息左对齐，灰色气泡。
  - AI 消息底部"复制 / 重新生成 / 插入回复编辑器"（已有"插入"，加上"复制"）。
  - 流式响应：当前后端非流式（`sendAssistantMessage` 直接返回完整 AssistantMessage），本次需求**升级为流式 SSE**（见 8.2）。
- **输入框**：
  - placeholder："询问当前页面或草稿…"
  - 提示"Shift+Enter 换行"灰字。
  - 发送按钮 loading 态。
- **左下角图标**（参考阿里云右侧浮动）：保留"插入"快捷入口可作为侧边小工具。

### 8.2 后端流式化（SSE）

- 新增 `POST /api/assistant/sessions/{id}/messages/stream`：服务端 SSE 推送 assistant 消息的增量 token；前端 EventSource / fetch + ReadableStream 消费。
- 非流式端点保留作为回退。
- 后端需复用现有 LLM Config 的 `protocol`（chat / responses / anthropic），按 `llm_config_id` 调用对应上游。
- 重新生成功能 = 流式端点带 `previous_message_id` 参数，重新组装 history。

### 8.3 LLM 选择与可配置

- 现状 [AssistantPanel.tsx:208-220](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/AssistantPanel.tsx#L208) 已有 LLM 配置下拉。
- 增强：
  - 显示 LLM 名称 + 模型 ID（sub 行），帮助用户判断是否选对。
  - 增加"默认 LLM" 配置项：写在 user 偏好 / 系统设置（不需要新表，写在 `SystemSetting` 中即可，key=`assistant.default_llm_config_id`）。
  - 当前面板的"新建会话用配置"下拉默认选中"默认 LLM"。

### 8.4 上下文采集增强

- 现状 [contextRegistry.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/contextRegistry.ts) 已为每个 feature 定义 `resource`。
- 增强：
  - `models` feature：resource 包含当前 `model_id`（若在详情页 / 编辑态）。
  - `llm_configs` feature：resource 包含当前选中 LLM 的 name、protocol、base_url_host（脱敏，不含 api_key / headers）。
  - `connections` feature：resource 包含 platform、state。
  - `account` / `users` feature：resource 包含 username（不含 email/avatar）。
  - 全部走白名单（与后端 redaction 一致；不增量采集其他字段）。

### 8.5 验收

- 打开小助手 → 看到欢迎区 + 3 张示例卡。
- 点击示例卡自动填入 → 发送 → 流式逐字回显。
- 复制按钮可复制 AI 完整回答。
- "插入回复编辑器" 仅在任务详情页（bridge 存在）时显示。
- 全屏模式可沉浸式对话。
- 管理员账号下不显示小助手（[AssistantPanel.tsx:147-150](file:///d:/pyPrj/human-llm-gateway/admin/src/features/assistant/AssistantPanel.tsx#L147) 行为保留）。

---

## 9. 导航与首页大改：控制台大屏 + 任务记录

**现状：**
- [navigation.ts:30-49](file:///d:/pyPrj/human-llm-gateway/admin/src/navigation.ts#L30) 工作台组下：控制台 (`/console`) + 任务工作台 (`/tasks`)。
- [DashboardPage.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/dashboard/DashboardPage.tsx) 只是 4 张 StatCard + 最近任务表，**没有大屏观感**。
- [TasksPage.tsx:64-69](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TasksPage.tsx#L64) 标题"任务工作台" + 描述"处理你的人工任务并提交回复"。

**需求：**

### 9.1 控制台：大屏模式（参考 Grafana / K8s 概览）

- 路径 `/console` 不变。
- 顶部 4 张大数字卡片（沿用现状 StatCard 视觉但放大尺寸：96px 高，卡片有"实时"角标 + 趋势 sparkline）。
- 主体两列：
  - 左列（2/3 宽）：
    - 任务量时间线（最近 7 天，柱状图 + 数字），使用轻量图表（不引入 chart 库；用纯 SVG 渲染或自绘 canvas）。
    - 协议分布饼图（OpenAI Chat / Responses / Anthropic 三种），同样纯 SVG。
    - 失败/超时任务 Top 5 列表（带跳转）。
  - 右列（1/3 宽）：
    - 系统设置 Quick Links（按角色能力显示）。
    - 待办（人工截止剩余时间最短的 5 个进行中任务，点击直达 task detail）。
    - 助手推荐语 + FAB（同需求 8）。
- 刷新策略：每 30s 后台拉取 `getDashboard()`（[api/logs.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/api/logs.ts)）；loading 状态优雅（保留旧数据 + 顶部进度条）。
- 管理员专属模块：全局 IM 连接健康一览（每行展示 platform / state / retry / last_error），点击进入 task detail / connection list。

### 9.2 任务工作台 → 任务记录

- 路径 `/tasks` 不变；title 改为 **"任务记录"**；description 改为"查看全部任务，回复归属于自己的进行中任务"。
- 区分**未读/进行中/已结束**：
  - 顶部 Segmented control：`全部 / 进行中 / 已完成 / 失败`，默认"进行中"。
  - 列表卡片每行加：过期倒计时徽标（`< 5min` 红色，`5-30min` 黄色，`> 30min` 灰色）。
- 操作列：
  - 任务所有者：只显示"详情"。
  - 其他人（管理员 + 进行中任务的所有者判断逻辑沿用 [TaskDetailDrawer.tsx:104-106](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TaskDetailDrawer.tsx#L104)）：显示"详情"。
  - **删除旧的"任务工作台"概念**：UI 中不再强调"工作"（处理），改为"记录"（查询 + 操作）。
- "任务详情"抽屉（[TaskDetailDrawer.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TaskDetailDrawer.tsx)）保留，沿用现状；"撰写回复"按钮按所有权判断显示。

### 9.3 导航结构调整

- [navigation.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/navigation.ts)：
  - "工作台"组只保留 1 项：**控制台**。
  - **新增"任务"组**：`任务记录`（`/tasks`）+ `任务统计`（`/tasks/stats`，可选，新建页面）。
  - 或更简单：把"任务记录"放到"工作台"组下作为第二项，沿用现有 pattern。
- 路由：用户访问 `/tasks` 仍进入 [TasksPage](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TasksPage.tsx)；仅文案调整。

### 9.4 验收

- `/console` 显示大屏：4 张大卡片 + 时间线 + 协议分布 + 待办 + 快速入口，桌面 1440px 宽度下 1 屏可见主要信息。
- `/tasks` 标题为"任务记录"，默认筛选进行中。
- 进行中任务列表中"任务编号 #0012"显示 4:32 倒计时，红色高亮。

---

## 10. 新增：网页端的回复页面（独立路由）

**背景：**
当前人工回复只能在 `TaskDetailDrawer`（[TaskDetailDrawer.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TaskDetailDrawer.tsx)）打开 [ReplyEditor.tsx](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/ReplyEditor.tsx)。Drawer 是浮层，体验受限；且**当前 ReplyEditor**只有"思考/假 Tool Call/最终文本"3 个 block，结构上无法独立选择类型。

**需求：**
**新增独立回复页面** `/tasks/:id/reply`（或 `/inbox/:id/reply`），与 Drawer 互斥。Drawer 入口保留作为快速回复，独立页面作为"沉浸式回复"。

### 10.1 路由

- `Route path="/tasks/:id/reply"` element 指向新建 `ReplyPage` 组件。
- 顶部返回按钮回到 `/tasks/:id` 详情（or Drawer 打开？沿用 Drawer 也可；最简：从 ReplyPage 提交完成后回 `/tasks`）。

### 10.2 页面布局

```
┌─────────────────────────────────────────────────────┐
│ [← 返回] 任务 #0012 · deepseek-v4-pro               │
│         状态: waiting_human · 截止 4:32             │
├──────────┬──────────────────────────────┬──────────┤
│  提示词   │  左侧 tabs:                 │ 工具调用  │
│          │  [ 思考链 ][ 正式回复 ]      │           │
│  原始    │                              │  + 添加   │
│  请求    │  ┌─────────────────────────┐ │           │
│          │  │ textarea                │ │  ┌──────┐ │
│          │  │                         │ │  │tool 1│ │
│          │  │                         │ │  │tool 2│ │
│          │  │                         │ │  └──────┘ │
│  事件    │  └─────────────────────────┘ │           │
│  时间线  │                              │  允许工具  │
│          │  [生成草稿][保存草稿][提交]  │  列表:     │
│          │                              │  ☑ search │
│          │                              │  ☑ calc   │
│          │                              │  ☑ image  │
└──────────┴──────────────────────────────┴──────────┘
```

- **左侧栏**（1/4 宽，sticky）：任务信息 + 提示词 + 原始请求 + 事件时间线。
- **中间**（1/2 宽）：tab 切换"思考链 / 正式回复 / 工具调用"（每个 tab 一个 textarea/block）。
  - **思考链**：与现状 [ReplyEditor.tsx:267-274](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/ReplyEditor.tsx#L267) 一致；不参与最终回复（保留语义）。
  - **正式回复**：与现状 `final_text` 一致；成为最终回复主输出。
  - **工具调用**：每个 tool call 是单独 block（id + name + arguments JSON）；可以从右侧"允许工具"清单拖入或点选自动填充 name。
- **右侧栏**（1/4 宽，sticky）：
  - "工具调用"列表显示当前已经添加的 tool calls。
  - "允许工具"区列出该 LLM Config / 该任务允许的 tool 列表（**取自 M12 工具沙箱的 `ToolWhitelist` + `Tool` 资源**），多选 checkbox。

### 10.3 数据结构

- 后端 `submitReply` / `saveDraft` 接口（[app/api/tasks.py](file:///d:/pyPrj/human-llm-gateway/app/api/tasks.py)）的入参 `ReplyDraft` 维持 `reasoning / tool_calls / final_text` 三字段——前端只把"tab"映射到这 3 字段，**不破坏后端契约**。
- tool_calls 构造：
  - 用户从右侧"允许工具"勾选 → 自动新增一条 `ToolCallEditor { id: nextCallId(), name: selected, argumentsText: '{}' }`。
  - 用户也可以手动新增（不勾选工具，但要求 `name` 必填）。
  - 后端继续按现有校验（id/name 必填、arguments 合法 JSON）。
- "允许工具"清单来源：后端 `app/api/tools.py` 的 `list_tools` 端点（`GET /api/tools`）；过滤 `enabled=true`，按 `risk_level` 排序显示。

### 10.4 交互

- Tabs 切换不丢失内容（用 local state 保存所有 3 个 block 的值）。
- "生成草稿"：沿用 [TaskDetailDrawer.tsx:108-123](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TaskDetailDrawer.tsx#L108) 的 LLM 生成逻辑；选 LLM Config → 调 `generateDraft` → 把结果分别填到"思考链 / 正式回复 / 工具调用"对应 tab。
- "保存草稿"：调 `saveDraft` / `updateDraft`（[api/tasks.ts](file:///d:/pyPrj/human-llm-gateway/admin/src/api/tasks.ts)）。
- "提交"：调 `submitReply`，成功后跳回 `/tasks`。
- 全部支持键盘快捷键：`Cmd/Ctrl + S` 保存草稿，`Cmd/Ctrl + Enter` 提交。
- 顶部倒计时实时刷新（与需求 9 任务记录中的倒计时同源）。

### 10.5 路由切换的衔接

- [TaskDetailDrawer.tsx:188](file:///d:/pyPrj/human-llm-gateway/admin/src/features/tasks/TaskDetailDrawer.tsx#L188) 的"撰写回复"按钮改为 `navigate("/tasks/${taskId}/reply")` 代替打开 Drawer（可加 option 走 Drawer，但默认走独立页）。
- 任务列表的"详情"按钮仍打开 Drawer（`/tasks` 上的 Drawer）。
- 独立页加载时复用 [getTask](file:///d:/pyPrj/human-llm-gateway/admin/src/api/tasks.ts) 拉取数据。

### 10.6 验收

- 打开 `/tasks/123/reply` 直接进入沉浸式回复界面。
- Tab 切换"思考链 / 正式回复 / 工具调用"互不干扰。
- 右侧勾选 1 个 tool → 中间 tab 切换到"工具调用"自动多了一行 block。
- 提交后任务状态变为 `completed`，回到 `/tasks`。
- F5 刷新页面 → 草稿仍在（已 saveDraft 的情况）。

---

## 11. 跨需求交付物清单

| 模块 | 主要改动文件（路径） |
| --- | --- |
| 1.1 强制改密 | `app/api/account.py` · `app/services/user_service.py` · `admin/src/features/auth/ForcePasswordPage.tsx` |
| 1.2 注册预填 | `admin/src/features/auth/RegisterPage.tsx` |
| 2. 文案清理 | `admin/src/components/feedback/DismissCard.tsx`（新增） · 各 PageHeader 处 |
| 3. 模型广场 | `app/repositories/models/catalog.py` · `app/services/fake_model_service.py` · `app/api/fake_models.py` · `admin/src/features/models/*` · `admin/src/types/gateway.ts` |
| 4. LLM 参数 | `app/repositories/models/llm.py` · `app/services/llm_config_service.py` · `app/services/llm_forward_service.py` · `app/protocols/*` · `admin/src/features/llm/*` · `admin/src/api/llmConfigs.ts` |
| 5. IM 扫码 | `admin/src/features/connections/QrLoginDrawer.tsx`（新增） · `admin/src/features/connections/ConnectionsPage.tsx` |
| 6. 头像 | `admin/src/features/settings/AccountPage.tsx` · `app/api/account.py` · `app/repositories/models/auth.py`（如需 ALTER） |
| 7. logo | `admin/public/favicon.svg` · `admin/index.html` · `admin/src/components/brand/Brand.tsx`（新增） |
| 8. 小助手 | `admin/src/features/assistant/AssistantPanel.tsx` · `app/api/assistant.py` · `app/services/assistant/service.py` |
| 9. 控制台+任务记录 | `admin/src/features/dashboard/DashboardPage.tsx` · `admin/src/features/tasks/TasksPage.tsx` · `admin/src/navigation.ts` |
| 10. 独立回复页 | `admin/src/features/tasks/ReplyPage.tsx`（新增） · `admin/src/App.tsx`（新增路由） · `admin/src/features/tasks/TaskDetailDrawer.tsx`（按钮改 navigate） |

---

## 12. 风险与不做的清单

**风险：**
- 需求 4 协议字段值变化（`openai_compatible` → `openai_chat`）涉及既有 LLM Config 数据；migration 在启动时执行，失败时应用启动中止。
- 需求 5 微信扫码对 `openilink` SDK 有依赖（`app/connectors/implementations/wecom_ilink.py:55-67`）；SDK 不支持扫码的版本需额外兜底（走回退 binding_code 流程）。
- 需求 10 把 drawer 改为独立路由会影响部分"打开回复中关闭 drawer"的旧状态，需在 ReplyPage 关闭时主动 `invalidate tasks` 缓存。

**不做（暂缓）：**
- 多语言 i18n。
- 引入图表库（控制台图表用 SVG 自绘）。
- 引入 UI 组件库（继续用现有 Tailwind 自绘 + 现有 components）。
- 改 OpenAI/Anthropic 协议本身在 `app/protocols/` 的实现（本次只让 LLM Config 决定走哪个编码器）。
- 微信个人号 / 公众号等其他平台扫码（仅 iLink 走扫码，其他沿用 binding_code）。
- 后端 PWA 化（仅 favicon / theme-color 同步）。
- 多租户 / 配额 / 计费。

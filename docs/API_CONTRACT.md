# Human LLM Gateway API 契约

> 文档状态：M1 目标契约
>
> 目标业务接口将在 M2-M9 分阶段实现，运维接口在 M10 完成。文末列出的当前接口只是 M0 过渡现状，不提供兼容承诺。
> 支持的推理格式仅限 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages。

## 1. 命名空间与职责

| 前缀 | 用途 | 鉴权 |
| --- | --- | --- |
| `/api/*` | 登录后的管理后台 API | 用户会话 Token |
| `/v1/*` | 外部 LLM 兼容 API | 用户创建的 API Key |
| `/connectors/*` | IM/Webhook/WebSocket/HTTP 连接器入口 | 每个连接独立凭据 |
| `/healthz` | 进程存活检查，不访问数据库或连接器 | 无 |
| `/readyz` | 就绪检查（定义见下） | 无或仅部署网络可达 |
| `/metrics` | Prometheus 格式基础运行指标 | 仅部署网络或独立监控凭据 |

管理 API 与推理 API 使用不同的鉴权依赖和错误映射。用户登录 Token 不能调用 `/v1/*`，外部 API Key 不能调用 `/api/*`。

### 1.1 `/readyz` 就绪条件

固定为 5 项，全部满足才返回 200：

1. 应用 startup 已完成。
2. 数据库可访问，`schema_version` 匹配，且最近一次写能力自测正常。写自测在 startup 和后台低频周期任务（如每 60 秒）中执行并缓存最后成功时间；`/readyz` 本身只读取缓存状态并检查新鲜度窗口，超过窗口才返回未就绪，不得为探测执行数据库写事务。
3. 主加密密钥加载成功，并能成功解密数据库中的加密自检 sentinel（发现“数据库恢复了但 `APP_SECRET` 用错”的配置漂移）。
4. 三个协议 adapter/renderer registry 初始化成功。
5. 任务运行时协调器、超时/fallback 协调器和 connector registry 已启动。

`/readyz` 不检查任何用户 IM 连接是否在线、不检查真实 LLM 连通性、不要求存在至少一个连接实例；单个用户连接故障不能使实例变为未就绪。各连接健康继续通过连接管理 API 单独展示。

### 1.2 `/metrics` 指标契约

使用 Prometheus exposition format（`Content-Type: text/plain; version=0.0.4`），M10 实现。首版只做低基数指标：

| 类型 | 指标 |
| --- | --- |
| Counter | HTTP 请求、推理请求、任务终态、上游调用、连接器重连。 |
| Histogram | HTTP 延迟、推理总耗时、人工等待时长、上游 LLM 耗时。 |
| Gauge | 全局活动任务数、pending outbox 数、按 platform + state 聚合的连接数量。 |

标签只允许有限枚举：`protocol` / `strategy` / `outcome` / `platform` / `state` / `status_class` / `surface`。禁止 `user_id`、`api_key_id`、`task_id`、`connection_id`、`model`、`base_url`、`error_message` 出现在 label 中——既防 Secret 泄露，也防 Prometheus cardinality 爆炸。

## 2. 通用管理 API 约定

### 2.1 基础格式

- Content-Type：`application/json; charset=utf-8`。
- 时间：带时区的 ISO 8601 UTC 字符串，例如 `2026-08-28T10:30:00Z`。
- 资源 ID：响应中使用字符串，避免前端和外部 SDK 的整数精度差异。
- 可选字段：未设置时返回 `null`；敏感字段不返回占位明文。
- 更新：使用 `PATCH`，只修改显式提交的字段。
- 删除：使用 `DELETE`；被其他资源引用时返回 409，不做隐式级联业务修改。
- 幂等操作：对已启用、已停用、已撤销等状态重复调用返回当前状态，不产生重复副作用。
- 请求体大小上限：管理 API JSON 请求体最大 1 MiB，`/v1/*` 推理请求最大 8 MiB。超限返回 413（见 §16.3），必须在完整 JSON 解析、鉴权准入和 RequestTask 创建之前拒绝；使用 chunked 传输且没有 `Content-Length` 时，读取过程中累计字节超限即中断并返回 413。该上限是网关层硬性边界，与 `previous_response_id` 历史展开的 2 MiB 规范化上下文上限相互独立。

### 2.2 列表与分页

所有可能增长的管理列表使用：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

通用查询参数：

| 参数 | 规则 |
| --- | --- |
| `page` | 从 1 开始，默认 1。 |
| `page_size` | 默认 20，最大 100。 |
| `search` | 可选，服务端明确声明可搜索字段。 |
| `sort` | 使用稳定字段名；未声明的值返回 400。 |
| `order` | `asc` 或 `desc`。 |

### 2.3 成功与错误

单资源成功直接返回资源对象；创建返回 201；无正文删除返回 204。动作类接口返回资源最新状态或明确结果对象。

管理 API 错误统一为：

```json
{
  "error": {
    "code": "validation_failed",
    "message": "人工超时时间必须在 10 到 1800 秒之间",
    "request_id": "req_01J...",
    "details": {
      "field": "human_timeout_seconds"
    }
  }
}
```

`details` 可省略；不得把堆栈、SQL、Secret、上游原文异常或内部路径放入响应。

| HTTP | 管理错误码 | 使用场景 |
| --- | --- | --- |
| 400 | `validation_failed`、`invalid_invitation` | 请求或业务参数错误。 |
| 401 | `unauthorized` | 未登录、Token 失效。 |
| 403 | `forbidden` | 已登录但没有权限。 |
| 404 | `not_found` | 资源不存在或为防越权而隐藏。 |
| 409 | `conflict` | 名称冲突、资源被引用、状态竞争失败。 |
| 422 | `schema_error` | JSON 类型或结构无法解析。 |
| 429 | `rate_limit_exceeded` | 管理端频率限制。 |
| 500 | `internal_error` | 已脱敏的内部错误。 |

## 3. 登录、注册与账号

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | 公开 | 用户名和密码登录。 |
| POST | `/api/auth/register` | 公开 | 使用邀请码注册普通用户。 |
| GET | `/api/auth/me` | 登录用户 | 返回当前用户、角色和能力，含 `must_change_password` 状态。 |
| POST | `/api/auth/logout` | 登录用户 | 使当前登录 Token 失效。 |
| PATCH | `/api/account/profile` | 完整会话 | 修改自己的显示名。 |
| POST | `/api/account/password` | 登录用户 | 校验旧密码后修改自己的密码。 |

注册请求：

```json
{
  "invitation_code": "invite-plain-text",
  "username": "alice",
  "display_name": "Alice",
  "password": "user-password"
}
```

邀请码不存在、已过期、已撤销或已达到使用次数时都返回 400，错误码为 `invalid_invitation`；响应不区分更细原因，避免批量探测邀请码状态。成功消费和用户创建必须原子完成。

`username` 是登录标识，仅允许 ASCII 模式 `[a-z0-9][a-z0-9._-]{2,63}`：服务端先 strip 再做 ASCII 小写归一并校验，数据库以普通唯一索引保证唯一；Unicode 名称、中文、Emoji 一律放 `display_name`。

### 3.1 密码策略

注册、修改密码、管理员重置和 CLI 创建统一适用：

- 最少 15 个 Unicode code points，最大支持至少 128；不强制大小写、数字或特殊字符组合。
- NFC 归一化后哈希；允许空格和 Unicode 字符。
- 拒绝常见弱密码、与用户名相同或近似、明显部署默认词的 blocklist。

### 3.2 受限会话

`must_change_password=true` 的用户（环境变量初始化的首个管理员、使用临时密码的 CLI 创建管理员、被管理员重置为临时密码的用户）登录成功后获得受限会话：

- 仅允许 `GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/account/password`。
- 其余 `/api/*` 返回 403 `forbidden`，响应体提示需要先修改密码。
- 前端登录后直接重定向到强制改密页面；改密成功后 `must_change_password` 置 false，会话立即恢复完整权限，无需重新登录。

## 4. 管理员治理 API

### 4.1 邀请码

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/invitations` | 分页查看邀请码前缀、状态、有效期和使用次数。 |
| POST | `/api/invitations` | 创建邀请码，明文只在本次响应中返回。 |
| GET | `/api/invitations/{id}` | 查看非敏感详情。 |
| PATCH | `/api/invitations/{id}` | 修改备注、有效期和最大使用次数。 |
| POST | `/api/invitations/{id}/revoke` | 立即撤销并保留可见记录。 |
| DELETE | `/api/invitations/{id}` | 仅对已撤销邀请码执行软删除；注册来源和审计继续保留。 |

创建字段：`note`、`expires_at`、`max_uses`。`max_uses` 必须大于 0；已使用次数不能因更新上限而变得非法。只有管理员可调用这些接口。

### 4.2 用户

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/users` | 管理员分页查看用户。 |
| POST | `/api/users` | 管理员直接创建普通用户。 |
| GET | `/api/users/{id}` | 查看用户非敏感详情和资源计数。 |
| PATCH | `/api/users/{id}` | 修改显示名、角色允许范围内的状态。 |
| POST | `/api/users/{id}/reset-password` | 生成或设置一次性新密码，不回显旧密码。 |

禁止通过用户接口把普通用户提升为管理员。管理员初始化和新增管理员属于部署级受控流程，不在普通后台 API 中开放；首次管理员来自环境变量，后续管理员由受控 CLI 创建并写入审计：

```powershell
uv run python -m app.cli admin create --username alice --display-name "Alice"
```

CLI 交互式创建使用 `getpass` 隐藏输入并要求二次确认，禁止 `--password` 明文参数（避免 shell history 与进程列表泄密）；自动化部署使用 `--password-stdin --yes` 从 stdin 读取密码，或使用 `--generate-password --yes` 由系统生成临时密码（两者互斥；CSPRNG 生成、满足密码策略、明文只在 stdout 显示一次、不写入日志或审计）。CLI 复用 UserService、密码策略、审计与应用配置；使用系统生成临时密码时新管理员 `must_change_password=true`。

管理员把 `is_active` 更新为 false 时，服务端立即撤销目标用户会话、停用其全部 API Key、终止全部活动任务并幂等释放任务名额。新登录和新推理请求返回 401；已准入请求只收到通用协议错误。禁止禁用最后一个有效管理员。

## 5. IM 连接 API

### 5.1 平台目录与 CRUD

| 方法 | 路径 | 普通用户 | 管理员 |
| --- | --- | --- | --- |
| GET | `/api/im-platforms` | 查看可创建平台和配置 Schema | 查看 |
| GET | `/api/im-connections` | 仅自己的 | 全部，含所有者摘要 |
| POST | `/api/im-connections` | 创建自己的 | 禁止 |
| GET | `/api/im-connections/{id}` | 自己的非敏感详情 | 任意非敏感详情 |
| PATCH | `/api/im-connections/{id}` | 修改自己的 | 仅允许非凭据治理字段 |
| DELETE | `/api/im-connections/{id}` | 删除自己的 | 删除任意用户连接 |

创建/更新配置由 `/api/im-platforms` 返回的平台 Schema 校验。Secret 字段写入后不回显；更新时省略 Secret 表示保留，显式提交新值表示替换，不允许用空字符串猜测语义。

### 5.2 生命周期和绑定

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/im-connections/{id}/start` | 启动目标连接。 |
| POST | `/api/im-connections/{id}/stop` | 停止目标连接。 |
| POST | `/api/im-connections/{id}/apply` | 应用保存的配置并只重启目标连接。 |
| GET | `/api/im-connections/{id}/health` | 返回数据库状态和瞬时运行状态。 |
| POST | `/api/im-connections/{id}/login` | 所有者发起需要交互的登录。 |
| GET | `/api/im-connections/{id}/login` | 所有者轮询登录进度和临时二维码。 |
| POST | `/api/im-connections/{id}/binding` | 所有者生成一次性绑定码。 |
| GET | `/api/im-connections/{id}/binding/status` | 所有者查看绑定结果。 |

管理员可启动、停止、应用和检查任意连接，但不能调用登录或绑定接口，也不能取得二维码和绑定码。

期望运行的长连接遇到普通网络故障后自动指数退避重连；进入 `auth_required` 后停止自动重试。重新登录由所有者执行，`apply` 只重启目标连接。

## 6. LLM 配置 API

目标资源名统一为 `llm-configs`，不再暴露 Provider、LLMModel 或 ModelRoute。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/llm-configs` | 用户查看自己的配置；管理员只能查看所有者和脱敏元数据。 |
| POST | `/api/llm-configs` | 用户创建配置。 |
| GET | `/api/llm-configs/{id}` | 查看自己的非敏感详情。 |
| PATCH | `/api/llm-configs/{id}` | 修改配置；省略 Secret 表示保留。 |
| DELETE | `/api/llm-configs/{id}` | 被有效 API Key 或活动任务引用时返回 409，否则清空 Secret 后软删除。 |
| POST | `/api/llm-configs/{id}/test` | 使用最小请求测试连通性，不回显 Secret。 |

主要字段：

```json
{
  "name": "我的上游",
  "protocol": "openai_compatible",
  "base_url": "https://example.com/v1",
  "api_key": "secret-on-write-only",
  "model": "real-model-name",
  "timeout_seconds": 120,
  "headers": {
    "X-Custom": "secret-on-write-only"
  },
  "enabled": true
}
```

`protocol` 初期支持 `openai_compatible`、`anthropic`。自定义 Header 整体按 Secret 处理，列表和详情只返回 header 名称，不返回值。

历史任务保留配置名称、协议、规范化 Base URL 和真实模型等非敏感快照，不保留 Secret、自定义 Header 值，也不能用已删除配置重新执行。

## 7. Fake Model 与模型分组 API

### 7.1 Fake Model

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/fake-models` | 登录用户 | 用户看系统模型和自己的私有模型；管理员看全部治理列表。 |
| POST | `/api/fake-models` | 登录用户 | 管理员创建系统模型，普通用户创建自己的私有模型。 |
| GET | `/api/fake-models/{id}` | 登录用户 | 返回权限范围内详情。 |
| PATCH | `/api/fake-models/{id}` | 登录用户 | 用户修改自己的私有模型；管理员治理全部并维护系统模型。 |
| DELETE | `/api/fake-models/{id}` | 登录用户 | 用户删除自己的私有模型；管理员可治理删除。 |

Fake Model 字段只描述对外目录，不包含 LLM 配置 ID、真实模型或回复策略。管理员创建的系统模型对全部用户可见；普通用户创建的私有模型只对所有者可见，其他普通用户即使猜到 ID 也返回 404。管理员治理私有模型时不能把它改绑或转授给其他用户。

### 7.2 模型分组（M5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/model-groups` | 用户查看或创建自己的分组；管理员可治理全部。 |
| GET/PATCH/DELETE | `/api/model-groups/{id}` | 用户维护自己的分组。 |
| PUT | `/api/model-groups/{id}/models` | 用当前用户可见的完整 Fake Model ID 集合原子替换成员。 |

模型分组是第一层可复用筛选：未绑定分组时，候选集为用户可见的全部有效模型；绑定后，候选集为其中仍属于分组的模型。分组不能引用其他用户的私有模型。

## 8. API Key API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/api-keys` | 用户看自己的 Key；管理员看脱敏全局列表。 |
| POST | `/api/api-keys` | 用户创建 Key，明文只返回一次。 |
| GET | `/api/api-keys/{id}` | 返回配置和 Key 前缀。 |
| PATCH | `/api/api-keys/{id}` | 修改名称、状态、入口和策略。 |
| DELETE | `/api/api-keys/{id}` | 立即阻止新请求并软删除 Key；已准入任务按创建快照继续完成。 |

目标写入结构：

```json
{
  "name": "演示调用",
  "enabled": true,
  "delivery_mode": "im",
  "im_connection_id": "im_123",
  "reply_strategy": "human_fallback_llm",
  "llm_config_id": "llm_456",
  "human_timeout_seconds": 300,
  "model_group_id": null,
  "fake_model_ids": []
}
```

规则：

- `delivery_mode` 为 `web` 或 `im`；`im` 必须选择当前用户有效连接。
- 任务无论入口为何都在 Web 可见且可回复。
- `reply_strategy` 为 `human`、`llm` 或 `human_fallback_llm`。
- `llm` 与 `human_fallback_llm` 必须选择当前用户有效 LLM 配置。
- 人工超时范围 10-1800 秒，默认 300 秒。
- `fake_model_ids` 只能来自模型分组预筛后的候选集；提交空数组或省略代表允许候选集中的全部模型。
- 显式选择一个或多个模型后，`/v1/models` 和推理请求只允许所选的有效模型。
- 管理员不能替用户创建 Key、取得明文或把 Key 改绑到其他用户资源。

## 9. 任务、草稿和回复 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks` | 用户查看自己的任务；管理员查看脱敏全局任务。 |
| GET | `/api/tasks/{id}` | 用户查看完整原始请求、时间线、草稿和结果。 |
| GET | `/api/tasks/{id}/events` | 分页查看任务事件。 |
| POST | `/api/tasks/{id}/drafts` | 新建或保存人工草稿。 |
| PATCH | `/api/tasks/{id}/drafts/{draft_id}` | 更新未提交草稿。 |
| DELETE | `/api/tasks/{id}/drafts/{draft_id}` | 删除未提交草稿。 |
| POST | `/api/tasks/{id}/drafts/generate` | 选择 LLM 配置生成持久化草稿。 |
| POST | `/api/tasks/{id}/reply` | 原子提交完整回复，首个有效提交获胜。 |

管理员只能查看允许的任务元数据和脱敏请求，不可调用草稿或回复写接口。

回复请求使用统一的协议无关 `ReplyDraft` 表示。Web 编辑器直接读写该结构，IM DSL 解析器也必须生成完全相同的结构：

```json
{
  "reasoning": "可选思考内容",
  "tool_calls": [
    {
      "id": "call_01",
      "name": "lookup",
      "arguments": {"id": 1}
    }
  ],
  "final_text": "最终回复",
  "source_draft_id": "draft_01"
}
```

`arguments` 必须是合法 JSON 值。系统为不同协议生成对应 tool call 结构，但绝不执行。提交前可以预览、编辑或丢弃草稿；提交成功后没有撤销接口，草稿不可继续修改。竞争失败返回 409 `task_already_resolved`，并记录晚到提交审计。

## 10. Web 小助手 API（M8）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/assistant/sessions` | 查看或创建自己的会话。 |
| GET | `/api/assistant/sessions/{id}` | 查看自己的会话与消息。 |
| DELETE | `/api/assistant/sessions/{id}` | 删除自己的会话和消息。 |
| POST | `/api/assistant/sessions/{id}/messages` | 使用选定 LLM 配置发送文本和当前页面上下文快照。 |

每次发送的上下文包含当前浏览器标签页的 route、feature、选中资源、上下文版本和当前未提交编辑内容的白名单摘要。切换页面或资源会替换待发送上下文，不自动携带旧页面数据；历史消息保留各自发送时已经过滤的快照。后端拒绝密码、完整 API Key、Authorization、Cookie、Token、Secret 和 IM/LLM 凭据。M8 第一阶段不提供可执行系统工具。

## 11. 设置、日志与审计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET/PATCH | `/api/settings` | 管理员 | 基础非 Secret 设置。 |
| GET | `/api/audit-logs` | 管理员 | 按操作者、资源、动作和时间筛选。 |
| GET | `/api/app-logs` | 管理员 | 按级别、事件、request/task/key/connection ID 筛选。 |

普通用户在任务时间线中只能看到自己的相关业务事件，不能直接读取全局应用日志。

审计 action 使用稳定枚举。管理员可读取操作者、动作、资源 ID、所有者、时间、结果和变更字段名；接口不得返回请求正文、字段值、Secret 旧值/新值或任何凭据恢复材料。

## 12. 推理 API 通用契约

### 12.1 鉴权

- `GET /v1/models`、OpenAI Chat 和 OpenAI Responses 使用 `Authorization: Bearer <API_KEY>`。
- Anthropic Messages 接受官方客户端使用的 `x-api-key: <API_KEY>`，并接收 `anthropic-version`。
- 服务端不得在错误、日志或追踪中输出完整 Key。
- `/v1/models` 不接受匿名访问，因为目录必须按 Key 所有者、模型分组和 Key 选择计算。SDK 或适配器必须先设置 Key，再进行模型发现；匿名请求稳定返回 401，而不是空目录。

### 12.2 通用准入顺序

1. 解析请求基本结构。
2. 验证 API Key 并确认启用状态。
3. 从用户可见模型、可选模型分组和 Key 显式模型选择计算有效集合，并校验 `model`。
4. 原子检查并占用 Key 所属用户的活动任务名额。
5. 完整保存原始请求。
6. 根据 Key 策略处理。

任何步骤失败都不能向 IM 投递，也不能调用真实 LLM。用户已有 10 个活动任务时，第 11 个请求直接返回 429。

### 12.3 Fake Model 目录

`GET /v1/models` 返回当前 Key 的有效 Fake Model 集合：

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-5",
      "object": "model",
      "created": 1787882400,
      "owned_by": "openai"
    }
  ]
}
```

计算顺序如下：

1. 取 Key 所有者可见且启用的系统模型和私有模型。
2. Key 绑定模型分组时，与组内模型取交集。
3. Key 显式选择 Fake Model 时，再与所选模型取交集。
4. Key 未显式选择任何模型时，保留上一步全部候选模型。

目录内容与该 Key 选择的真实 LLM 配置无关。任何筛选层只能收窄上一层结果，不能让用户访问其他用户的私有模型。

### 12.4 请求保真

- 原始 JSON payload 完整落库。
- 同协议真实 LLM 转发默认保留全部调用方字段和未知扩展字段。
- 服务端不移除 tools、tool choice、metadata 或供应商扩展。
- 身份 system 指令追加在调用方已有 system 内容之后。
- API 契约明确声明的网关控制字段可以被校验、消费或等价重写，但必须完整保存在原始 payload，并记录转换决定。
- 跨协议只转换可等价语义；存在不可转换的专有字段时返回 400。
- 任何上游返回的 tool call 只转发，不由本系统执行。

### 12.5 网关控制字段

OpenAI Responses 的 `previous_response_id` 由网关提供语义，而不是机械发送给用户配置的真实 LLM：

1. 所有通过准入并创建任务的 OpenAI Responses 请求，在任务创建事务中生成并持久化稳定的网关 response ID（格式为 `resp_` + 32 位小写 hex），并关联当前任务和 API Key；发送第一个 Responses 响应事件之前该 ID 必须已经存在，`response.failed` 等失败终态事件沿用同一 ID。
2. 新请求携带 `previous_response_id` 时，只允许引用同一 API Key 的历史响应；只有状态为 COMPLETED 的 response ID 可被引用——不存在、属于其他 Key、尚未完成、已失败或取消、或形成非法链时返回 400 `invalid_previous_response_id`。
3. 网关完整保存本次原始 payload 和原始 ID，加载历史规范化请求与响应，并按时间顺序等价展开成本次上下文。展开具有唯一语义：链 A→B→C 时 A 不会被重复展开。
4. 人工流程向用户展示展开后的上下文；真实 LLM 转发使用展开后的消息，不把无法识别的网关 ID 发送给上游。
5. 展开结果受三重网关硬性保护，任一超限整请求返回协议兼容 400 `context_length_exceeded`，不静默截断：
   - `max_chain_depth = 20`（沿 `previous_task_id` 可追溯的历史祖先节点数上限，当前请求不计入）；
   - `max_expanded_items = 512`（展开后规范化顶级上下文条目累计上限：一条 message、一个 tool call、一个 reasoning 项等各计 1 条，message 内多个 content block 合并计 1 条；三种协议共用同一预算函数）；
   - `max_expanded_context_bytes = 2 MiB`（规范化展开 JSON 的 compact UTF-8 字节上限，`ensure_ascii=false`、无缩进，序列化参数固定）。
6. Fake Model 与真实模型解耦，网关不在准入阶段估算 token；M7 由协议 adapter 处理真实模型 token 限制（本地 tokenizer 预检或映射上游超限错误），不强制每次推理调用远程 `count_tokens`。
7. 历史清理不得破坏仍被引用的链；可以保留最小非敏感上下文快照代替完整任务，但不能留下悬空 ID。

这是一项公开声明的网关等价转换，不违反原始请求完整落库原则。除契约明确列出的控制字段、服务端身份 system 指令和上游真实模型替换外，同协议字段仍默认原样透传。

### 12.6 跨协议字段转换矩阵

每个字段只能采用四种处理：`透传`、`等价转换`、`网关消费`、`拒绝 400`。禁止使用“忽略”“尽量转换”或把未知字段塞进 metadata 冒充等价支持。

| 语义 | OpenAI Chat | OpenAI Responses | Anthropic Messages | 跨协议规则 |
| --- | --- | --- | --- | --- |
| 系统指令 | system/developer message | `instructions` 或输入项 | 顶级 `system` | 按原有顺序转换，再在末尾追加 Fake Model 身份指令。 |
| 用户/助手内容 | `messages` | `input` message/items | `messages` content blocks | 文本和角色等价转换；不支持的内容块返回 400。 |
| Fake Model | `model` | `model` | `model` | 用于权限校验和对外身份；上游请求使用 LLM 配置的真实模型，响应改回 Fake Model。 |
| 输出上限 | `max_completion_tokens` 或兼容 `max_tokens` | `max_output_tokens` | `max_tokens` | 数值与边界等价转换；同一请求同时给出冲突字段时返回 400。 |
| 采样参数 | `temperature`, `top_p` | `temperature`, `top_p` | `temperature`, `top_p` | 目标协议支持且范围兼容时转换，否则 400。 |
| 停止序列 | `stop` | 适配器声明的等价字段 | `stop_sequences` | 字符串转单元素数组；数量或限制超出目标能力时返回 400。 |
| 流式开关 | `stream` | `stream` | `stream` | 布尔值等价转换；事件结构由目标协议渲染器负责。 |
| 函数工具 Schema | `tools[].function.parameters` | function tool parameters | `tools[].input_schema` | JSON Schema 子集等价转换；目标不支持的关键字或托管工具类型返回 400。 |
| 工具选择 | `none/auto/required/指定函数` | 对应 function tool choice | `none/auto/any/tool` | `required` ↔ `any`，指定函数 ↔ `tool{name}`；其他不可等价值返回 400。 |
| 并行工具控制 | `parallel_tool_calls` | `parallel_tool_calls` | `disable_parallel_tool_use` | 布尔语义取反转换；目标版本不支持时返回 400。 |
| 工具调用/结果 | assistant tool_calls / tool role | function_call / function_call_output | tool_use / tool_result | 保留 call ID、name、JSON arguments 和结果配对；结构不完整返回 400。 |
| reasoning 输出 | `reasoning_content` 兼容字段 | reasoning output item | thinking content block | 已生成文本可进入统一 reply schema；签名等不可伪造字段不转换。 |
| reasoning 请求控制 | 供应商扩展 | `reasoning` 等控制 | thinking/budget 配置 | 只有矩阵后续明确证明等价的组合才转换，其他跨协议请求返回 400。 |
| JSON/结构化输出 | `response_format` / json_schema | text format/json schema | 无通用等价字段 | OpenAI 两格式间可按 Schema 转换；转 Anthropic 时返回 400，不用提示词伪装等价支持。 |
| metadata/用户标识 | `user`, `metadata` 或扩展 | `metadata`, `safety_identifier` 等 | `metadata.user_id` | 仅已列出的等价键可转换；额外键不静默丢弃，返回 400。 |
| 历史响应引用 | 无 | `previous_response_id` | 无 | 由网关消费并展开，遵循 12.5。 |
| Prompt Cache | 供应商扩展 | 供应商扩展 | `cache_control` | 同协议原样透传；跨协议没有明确等价项时返回 400。 |
| 托管工具和文件能力 | 供应商专有类型 | file search/computer 等 item | 供应商专有 block | 只有目标适配器明确实现同等能力才转换；默认返回 400，系统绝不执行。 |
| `service_tier` 等计费层参数 | 供应商字段 | 供应商字段 | 供应商字段 | 同协议透传；跨协议未逐项声明等价时返回 400。 |
| `background`（Responses 后台模式） | 无 | 供应商字段 | 无 | 网关不提供后台响应生命周期接口（无 `GET/cancel` 响应端点），透传会使 RequestTask 无法正确收尾；显式 `background=true` 返回 400 `unsupported_parameter`。 |
| `conversation`（Responses 服务端会话引用） | 无 | 供应商字段 | 无 | 引用上游服务端持久状态，不能机械转给用户自己的真实上游；显式提交返回 400 `unsupported_parameter`。 |
| `store`（响应持久化开关） | 供应商字段 | 供应商字段 | 无 | 网关始终完整持久化 RequestTask，`store` 不是网关数据库的存储开关；显式提交任何值（含 `false`）返回 400 `unsupported_parameter`，避免“看似兼容、语义不同”。 |
| 未知扩展字段 | 原样保留 | 原样保留 | 原样保留 | 同协议原样透传；跨协议返回 400 `unsupported_parameter`。 |

转换适配器必须为每个非透传字段记录字段名、处理类型和结果，不记录字段值。新增支持前先更新此矩阵和契约测试。

## 13. OpenAI Chat Completions

### 13.1 请求

`POST /v1/chat/completions`

最低要求：`model` 为非空字符串，`messages` 为非空数组。其余字段完整保存；人工处理只读取必要的规范化投影。显式提交 `store` 按 12.6 矩阵返回 400 `unsupported_parameter`。

### 13.2 非流式响应

响应遵循 `chat.completion` 结构。人工 reasoning 使用兼容字段 `reasoning_content`；模拟工具使用 `message.tool_calls`；`model` 始终为请求 Fake Model。

### 13.3 流式响应

人工伪流式顺序：

1. 首块提供 `role: assistant`。
2. reasoning 使用 `delta.reasoning_content` 分块。
3. tool call 使用稳定 index/id/name 和 arguments 增量。
4. 最终文本使用 `delta.content` 分块。
5. 结束块给出正确 `finish_reason`，随后发送 `data: [DONE]`。

完整人工结果在发送第一块之前已经持久化。真实 LLM 流式则边接收边透传或转换。

## 14. OpenAI Responses

### 14.1 请求

`POST /v1/responses`

最低要求：`model` 为非空字符串，`input` 为有效字符串或输入项数组。`instructions`、tools 和所有扩展字段完整保存。

`previous_response_id` 按 12.5 由网关解析；调用方不需要知道真实上游 response ID。显式提交 `background`、`conversation` 或 `store` 按 12.6 矩阵返回 400 `unsupported_parameter`；本网关不提供后台响应 retrieve/cancel 等生命周期端点。

### 14.2 输出项

- reasoning 映射为 reasoning output item。
- 最终文本映射为 assistant message 的 `output_text` content。
- 模拟工具映射为 `function_call` output item，保留 call ID、name 和 JSON arguments。
- 完成响应和每个事件中的模型身份使用请求 Fake Model。

### 14.3 流式事件

事件至少遵循：`response.created`、`response.in_progress`、output item/content part 的 added/delta/done、`response.completed`。流在 `response.completed` 后正常结束，不混用 Chat Completions 的 `[DONE]` 作为业务事件。

## 15. Anthropic Messages

### 15.1 请求

`POST /v1/messages`

最低要求：`model`、非空 `messages` 和合法 `max_tokens`。接收官方 `x-api-key` 与 `anthropic-version` 请求头。`system` 可为字符串或内容块；原始结构完整保存。

`cache_control` 等 Anthropic 专有块在同协议转发时完整保留；人工流程只保存而不执行其缓存语义；跨协议按 12.6 的矩阵处理。

### 15.2 非流式响应

返回 `type: message`、`role: assistant` 和内容块数组：

- reasoning 映射为 thinking 内容块；
- 最终文本映射为 text 内容块；
- 模拟工具映射为 `tool_use` 内容块；
- `model` 改写为请求 Fake Model；
- `stop_reason` 根据最终内容或 tool use 选择兼容值。

### 15.3 流式事件

事件顺序遵循 Anthropic Messages：`message_start`，每个块的 `content_block_start`、一个或多个 `content_block_delta`、`content_block_stop`，随后 `message_delta` 和 `message_stop`。tool arguments 使用 `input_json_delta` 逐步传输。

## 16. 推理错误

### 16.1 OpenAI 风格

Chat、Responses 和 `/v1/models` 在尚未开始 SSE 时返回：

```json
{
  "error": {
    "message": "The requested model does not exist or is not available.",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found"
  }
}
```

### 16.2 Anthropic 风格

```json
{
  "type": "error",
  "error": {
    "type": "not_found_error",
    "message": "The requested model does not exist or is not available."
  },
  "request_id": "req_01J..."
}
```

### 16.3 状态映射

| 场景 | HTTP | 稳定语义 |
| --- | --- | --- |
| JSON、字段或不可转换参数错误 | 400 | `invalid_request_error` |
| API Key 无效、停用或所属用户被禁用 | 401 | `authentication_error` |
| 请求体超过大小上限（见 §2.1） | 413 | OpenAI `invalid_request_error`、Anthropic `request_too_large` |
| Fake Model 不存在、停用或不在 Key 有效集合 | 404 | `model_not_found` |
| 用户活动任务已达 10 | 429 | `rate_limit_exceeded` |
| 人工等待超时且无可用 fallback | 504 | 通用 timeout 错误，不暴露人工等待细节 |
| IM 投递、上游真实 LLM 或内部失败 | 500 | 通用服务错误 |

一旦 SSE 响应头已经发出，错误使用目标协议允许的流内错误事件并立即结束。对外消息不出现人工、IM、真实供应商、fallback、数据库或内部堆栈。

### 16.4 SSE 中断语义

流式响应头已发送后发生不可恢复错误（用户被禁用、上游失败、内部错误或服务关闭）时，必须按目标协议发送失败终态，绝不能伪造正常完成。对外只显示 generic error，不泄露 `user_disabled`、IM、fallback、真实供应商或内部路径。

| 协议 | 中断处理 |
| --- | --- |
| OpenAI Responses | 发送 `response.failed` 事件，沿用同一 `resp_...` ID，公开状态 `failed`，错误类型 generic `server_error`；随后结束流。不得再发送 `response.completed`。 |
| Anthropic Messages | 发送 `event: error`，内容使用 generic `api_error`；随后结束流。不得再发送 `message_stop`。Anthropic 官方明确允许 HTTP 200 后在 SSE 内出现 `event: error`。 |
| OpenAI Chat Completions | 不得伪造正常完成或发送 `[DONE]`。网关应使用经项目锁定版本 OpenAI SDK 契约测试验证的流内错误表示；若目标 SDK 无可靠的流内错误表示，则直接终止流。具体错误帧格式由 M6-A 兼容测试固化。 |

如果传输层已经不可写，则直接断流即可。数据库按真实内部原因进入对应终态：明确取消、用户被禁用和服务关闭进入 `CANCELLED` 并记录内部 `cancel_reason_code`（如 `user_disabled`）；上游失败、协议转换失败和内部处理错误进入 `FAILED` 并记录内部错误码。两者对外均只返回目标协议允许的通用错误，不泄露内部原因。不得把全部失败统一记为 `CANCELLED`，否则错误率指标、故障排查、重试判断和审计都会失真。

M6-A 必须使用项目锁定的 `openai` Python SDK 实际调用 Chat Completions 流，验证以下场景的 SDK 行为，并以真实结果决定 Chat 最终采用 `error frame + EOF` 还是单纯 `EOF`：

- 正常完成。
- 中途 generic error frame。
- 无 error frame 直接断流。
- 客户端主动取消。

SDK 升级时必须重新运行该契约测试。Responses 和 Anthropic 因协议本身有明确失败事件，直接按上表固定，无需依赖 SDK 行为验证。

## 17. 连接器协议

连接器入口不是公开 LLM API，每个连接使用独立 Token，且只能操作该连接所有者的任务。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/connectors/webhook/{connection_id}/inbound` | 自定义 Webhook 提交绑定消息或任务回复。 |
| WS | `/connectors/ws/{connection_id}` | 自定义 WebSocket 双向接收任务和回复。 |
| GET | `/connectors/http/{connection_id}/tasks` | 自定义 HTTP 按 cursor 拉取任务。 |
| POST | `/connectors/http/{connection_id}/replies` | 自定义 HTTP 提交回复。 |
| POST | `/connectors/http/{connection_id}/ack` | 可选 ACK 已取得任务。 |

HTTP 轮询响应返回单调 cursor；重复 cursor、ACK 或回复必须幂等。入站消息要求外部消息 ID，数据库以 `connection_id + external_message_id` 全局去重。

## 18. 当前 M0 过渡接口

以下接口描述当前代码，只用于界定 M2 必须一次性删除的范围，不代表需要兼容：

| 当前接口 | 目标处理 |
| --- | --- |
| `/api/providers*` | 删除，以 `/api/llm-configs*` 替换。 |
| `/api/model-routes*` | 删除；策略字段直接进入 API Key。 |
| `/api/model-catalog*` | 删除，以 `/api/fake-models*` 替换。 |
| `POST /api/*/{id}/update` | 删除，以 `PATCH` 替换。 |
| `POST /api/*/{id}/delete` | 删除，以 `DELETE` 替换。 |
| `POST /api/api-keys/{id}/disable` | 删除，以 `PATCH {"enabled": false}` 替换。 |
| 当前 `/v1/*` 由 `ModelRoute` 选择行为 | 重建为 API Key 策略、Fake Model 校验和用户级并发准入。 |

M2 的一个完整提交必须同时切换目标 Schema、服务、API、前端和测试，并删除这些接口及其旧模型。不得提交新旧表或运行链路共存的中间状态，也不得为了旧前端或旧数据库保留代理路由、字段别名、双写或自动迁移。

## 19. 契约变更要求

- 实现或修改接口前，先更新本文件和对应阶段路线图。
- 推理响应变更必须增加三协议契约测试和流式事件顺序测试。
- 管理 API 变更必须同步 TypeScript 类型与前端调用层。
- 新错误必须使用稳定错误码，并测试不泄露 Secret 和内部实现。
- 不得让当前过渡接口反向改变目标契约。

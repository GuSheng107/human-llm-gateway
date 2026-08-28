# Human LLM Gateway API 契约

> 文档状态：M1 目标契约
>
> 目标接口将在 M2-M9 分阶段实现。文末列出的当前接口只是 M0 过渡现状，不提供兼容承诺。
> 支持的推理格式仅限 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages。

## 1. 命名空间与职责

| 前缀 | 用途 | 鉴权 |
| --- | --- | --- |
| `/api/*` | 登录后的管理后台 API | 用户会话 Token |
| `/v1/*` | 外部 LLM 兼容 API | 用户创建的 API Key |
| `/connectors/*` | IM/Webhook/WebSocket/HTTP 连接器入口 | 每个连接独立凭据 |
| `/healthz` | 进程存活检查 | 无 |

管理 API 与推理 API 使用不同的鉴权依赖和错误映射。用户登录 Token 不能调用 `/v1/*`，外部 API Key 不能调用 `/api/*`。

## 2. 通用管理 API 约定

### 2.1 基础格式

- Content-Type：`application/json; charset=utf-8`。
- 时间：带时区的 ISO 8601 UTC 字符串，例如 `2026-08-28T10:30:00Z`。
- 资源 ID：响应中使用字符串，避免前端和外部 SDK 的整数精度差异。
- 可选字段：未设置时返回 `null`；敏感字段不返回占位明文。
- 更新：使用 `PATCH`，只修改显式提交的字段。
- 删除：使用 `DELETE`；被其他资源引用时返回 409，不做隐式级联业务修改。
- 幂等操作：对已启用、已停用、已撤销等状态重复调用返回当前状态，不产生重复副作用。

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
| GET | `/api/auth/me` | 登录用户 | 返回当前用户、角色和能力。 |
| POST | `/api/auth/logout` | 登录用户 | 使当前登录 Token 失效。 |
| PATCH | `/api/account/profile` | 登录用户 | 修改自己的显示名。 |
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

## 4. 管理员治理 API

### 4.1 邀请码

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/invitations` | 分页查看邀请码前缀、状态、有效期和使用次数。 |
| POST | `/api/invitations` | 创建邀请码，明文只在本次响应中返回。 |
| GET | `/api/invitations/{id}` | 查看非敏感详情。 |
| PATCH | `/api/invitations/{id}` | 修改备注、有效期和最大使用次数。 |
| POST | `/api/invitations/{id}/revoke` | 撤销邀请码。 |
| DELETE | `/api/invitations/{id}` | 删除持久化记录；已产生用户关系时保留审计快照。 |

创建字段：`note`、`expires_at`、`max_uses`。`max_uses` 必须大于 0；已使用次数不能因更新上限而变得非法。只有管理员可调用这些接口。

### 4.2 用户

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/users` | 管理员分页查看用户。 |
| POST | `/api/users` | 管理员直接创建普通用户。 |
| GET | `/api/users/{id}` | 查看用户非敏感详情和资源计数。 |
| PATCH | `/api/users/{id}` | 修改显示名、角色允许范围内的状态。 |
| POST | `/api/users/{id}/reset-password` | 生成或设置一次性新密码，不回显旧密码。 |

禁止通过用户接口把普通用户提升为管理员。管理员初始化和新增管理员属于部署级受控流程，不在普通后台 API 中开放。

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

## 6. LLM 配置 API

目标资源名统一为 `llm-configs`，不再暴露 Provider、LLMModel 或 ModelRoute。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/llm-configs` | 用户查看自己的配置；管理员只能查看所有者和脱敏元数据。 |
| POST | `/api/llm-configs` | 用户创建配置。 |
| GET | `/api/llm-configs/{id}` | 查看自己的非敏感详情。 |
| PATCH | `/api/llm-configs/{id}` | 修改配置；省略 Secret 表示保留。 |
| DELETE | `/api/llm-configs/{id}` | 删除未被有效 API Key 使用的配置。 |
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

### 7.2 模型分组（M10）

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
| DELETE | `/api/api-keys/{id}` | 停用并删除 Key；历史任务保留脱敏引用。 |

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

回复请求使用统一的协议无关表示：

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

`arguments` 必须是合法 JSON 值。系统为不同协议生成对应 tool call 结构，但绝不执行。提交成功后草稿不可继续修改；竞争失败返回 409 `task_already_resolved`，并记录晚到提交审计。

## 10. Web 小助手 API（M8）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/assistant/sessions` | 查看或创建自己的会话。 |
| GET | `/api/assistant/sessions/{id}` | 查看自己的会话与消息。 |
| DELETE | `/api/assistant/sessions/{id}` | 删除自己的会话和消息。 |
| POST | `/api/assistant/sessions/{id}/messages` | 使用选定 LLM 配置发送文本和白名单页面上下文。 |

后端忽略或拒绝上下文中的密码、完整 API Key、Authorization、Cookie、Token、Secret 和 IM/LLM 凭据。M8 第一阶段不提供可执行系统工具。

## 11. 设置、日志与审计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET/PATCH | `/api/settings` | 管理员 | 基础非 Secret 设置。 |
| GET | `/api/audit-logs` | 管理员 | 按操作者、资源、动作和时间筛选。 |
| GET | `/api/app-logs` | 管理员 | 按级别、事件、request/task/key/connection ID 筛选。 |

普通用户在任务时间线中只能看到自己的相关业务事件，不能直接读取全局应用日志。

## 12. 推理 API 通用契约

### 12.1 鉴权

- `GET /v1/models`、OpenAI Chat 和 OpenAI Responses 使用 `Authorization: Bearer <API_KEY>`。
- Anthropic Messages 接受官方客户端使用的 `x-api-key: <API_KEY>`，并接收 `anthropic-version`。
- 服务端不得在错误、日志或追踪中输出完整 Key。

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
- 同协议真实 LLM 转发保留全部调用方字段和未知扩展字段。
- 服务端不移除 tools、tool choice、metadata 或供应商扩展。
- 身份 system 指令追加在调用方已有 system 内容之后。
- 跨协议只转换可等价语义；存在不可转换的专有字段时返回 400。
- 任何上游返回的 tool call 只转发，不由本系统执行。

## 13. OpenAI Chat Completions

### 13.1 请求

`POST /v1/chat/completions`

最低要求：`model` 为非空字符串，`messages` 为非空数组。其余字段完整保存；人工处理只读取必要的规范化投影。

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
| API Key 无效或停用 | 401 | `authentication_error` |
| Fake Model 不存在、停用或不在 Key 有效集合 | 404 | `model_not_found` |
| 用户活动任务已达 10 | 429 | `rate_limit_exceeded` |
| 人工超时、IM/上游或内部失败 | 500，或按后续降级策略统一 429 | 通用服务错误 |

一旦 SSE 响应头已经发出，错误使用目标协议允许的流内错误事件并立即结束。对外消息不出现人工、IM、真实供应商、fallback、数据库或内部堆栈。

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

以下接口描述当前代码，便于 M2 删除和替换，不代表需要兼容：

| 当前接口 | 目标处理 |
| --- | --- |
| `/api/providers*` | 删除，以 `/api/llm-configs*` 替换。 |
| `/api/model-routes*` | 删除；策略字段直接进入 API Key。 |
| `/api/model-catalog*` | 删除，以 `/api/fake-models*` 替换。 |
| `POST /api/*/{id}/update` | 删除，以 `PATCH` 替换。 |
| `POST /api/*/{id}/delete` | 删除，以 `DELETE` 替换。 |
| `POST /api/api-keys/{id}/disable` | 删除，以 `PATCH {"enabled": false}` 替换。 |
| 当前 `/v1/*` 由 `ModelRoute` 选择行为 | 重建为 API Key 策略、Fake Model 校验和用户级并发准入。 |

后续不得为了旧前端或旧数据库保留代理路由、字段别名、双写或自动迁移。

## 19. 契约变更要求

- 实现或修改接口前，先更新本文件和对应阶段路线图。
- 推理响应变更必须增加三协议契约测试和流式事件顺序测试。
- 管理 API 变更必须同步 TypeScript 类型与前端调用层。
- 新错误必须使用稳定错误码，并测试不泄露 Secret 和内部实现。
- 不得让当前过渡接口反向改变目标契约。

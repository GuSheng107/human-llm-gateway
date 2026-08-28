# Human LLM Gateway 数据库设计

> 文档状态：M1 目标 Schema 设计
>
> M2 将按本文件直接重建 SQLAlchemy 模型。项目不做历史数据库迁移，也不为旧表或旧字段补兼容逻辑。

## 1. 存储原则

- 初期数据库为 SQLite，SQLAlchemy 负责模型和事务。
- 数据库文件不存在时，应用自动创建目录、全部表、索引、管理员账号、系统设置和默认系统 Fake Model。
- 已存在数据库的 `schema_version` 与代码不一致时启动失败，并提示备份后重新初始化；禁止自动 `ALTER TABLE` 猜测旧结构。
- 外键始终启用；推荐启用 WAL 和合理的 busy timeout。
- 网络、IM 和真实 LLM 调用不得发生在数据库写事务中。
- 关键竞争由条件 SQL 和唯一约束裁决，不依赖单进程内存锁。
- 所有时间以 UTC 存储，应用边界返回 ISO 8601。

## 2. 类型和命名

### 2.1 主键与外键

初期使用 SQLite `INTEGER PRIMARY KEY` 作为内部主键。管理 API 把 ID 序列化为字符串；外部推理对象使用单独生成的协议 ID，不把数据库自增 ID 暴露为 OpenAI/Anthropic 对象 ID。

所有外键列命名为 `<resource>_id`。历史任务引用的资源允许软删除或保存快照，禁止因删除配置而删除历史任务。

### 2.2 通用列

按资源需要复用：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `created_at` | datetime | 创建时间，不为空。 |
| `updated_at` | datetime | 最后更新时间，不为空。 |
| `deleted_at` | datetime nullable | 软删除时间；存在时普通查询不可见。 |
| `version` | integer | 乐观并发版本，从 1 开始。 |

### 2.3 枚举

枚举以稳定小写字符串存储，并使用 SQLAlchemy Enum 或 CHECK 约束：

| 枚举 | 值 |
| --- | --- |
| `UserRole` | `admin`, `user` |
| `ConnectionState` | `stopped`, `starting`, `online`, `auth_required`, `error` |
| `LLMProtocol` | `openai_compatible`, `anthropic` |
| `FakeModelScope` | `system`, `private` |
| `DeliveryMode` | `web`, `im` |
| `ReplyStrategy` | `human`, `llm`, `human_fallback_llm` |
| `InferenceProtocol` | `openai_chat`, `openai_responses`, `anthropic_messages` |
| `TaskState` | `received`, `waiting_human`, `forwarding_llm`, `response_ready`, `responding`, `completed`, `failed`, `timed_out`, `cancelled` |
| `DraftSource` | `manual`, `llm` |
| `DraftState` | `editing`, `submitted`, `discarded` |
| `AssistantRole` | `system`, `user`, `assistant` |

业务代码引用枚举成员，不散落裸字符串。

## 3. 身份与访问

### 3.1 `users`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `username` | varchar(64) | 非空，大小写归一后唯一 |
| `display_name` | varchar(100) | 非空 |
| `password_hash` | varchar(255) | 非空，自适应密码哈希 |
| `role` | enum | 非空，默认 `user` |
| `is_active` | boolean | 非空，默认 true |
| `active_task_count` | integer | 非空，默认 0，CHECK 0-10 |
| `registered_via_invitation_id` | integer nullable | FK invitation_codes，删除时 SET NULL |
| `last_login_at` | datetime nullable | 最近登录 |
| `created_at` / `updated_at` | datetime | 非空 |

索引：

- 唯一索引 `lower(username)`。
- `(role, is_active)` 管理筛选索引。

`active_task_count` 是并发准入的事务计数器，不通过扫描任务表决定第 11 个请求。后台可提供只读一致性检查，将计数与活动任务实际数量对比，但不能在普通请求中静默修正。

### 3.2 `auth_sessions`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `user_id` | integer | FK users，非空 |
| `token_hash` | varchar(255) | 非空，唯一 |
| `token_prefix` | varchar(16) | 审计展示，不可用于认证 |
| `expires_at` | datetime | 非空 |
| `revoked_at` | datetime nullable | 登出或强制失效 |
| `last_seen_at` | datetime nullable | 最近使用 |
| `created_at` | datetime | 非空 |

索引：`(user_id, revoked_at, expires_at)`。

### 3.3 `invitation_codes`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `created_by_user_id` | integer | FK users，必须是管理员 |
| `code_hash` | varchar(255) | 非空，唯一；不保存明文 |
| `code_prefix` | varchar(16) | 非空，用于后台识别 |
| `note` | varchar(255) nullable | 管理员备注 |
| `max_uses` | integer | 非空，CHECK > 0 |
| `used_count` | integer | 非空，默认 0，CHECK 0 <= used_count <= max_uses |
| `expires_at` | datetime nullable | 空表示不按时间过期 |
| `revoked_at` | datetime nullable | 撤销时间 |
| `deleted_at` | datetime nullable | 管理员删除后隐藏并禁止消费 |
| `created_at` / `updated_at` | datetime | 非空 |

索引：`(expires_at, revoked_at, deleted_at)`、`created_by_user_id`。

明文邀请码只在创建响应展示一次。删除采用软删除，保证既满足管理端删除能力，又能保留用户来源和审计证据。

## 4. 用户连接与真实 LLM

### 4.1 `im_connections`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `owner_user_id` | integer | FK users，非空 |
| `name` | varchar(100) | 非空 |
| `platform` | varchar(50) | 注册表平台代码，非空 |
| `config_ciphertext` | text | 整体认证加密配置，非空 |
| `config_key_version` | integer | 加密密钥版本，非空 |
| `desired_running` | boolean | 用户期望运行状态 |
| `state` | enum | 最后持久状态 |
| `bound_external_user_id` | varchar(255) nullable | 已绑定平台用户 ID，可加密或不可逆标记视平台风险决定 |
| `binding_code_hash` | varchar(255) nullable | 一次性绑定码哈希 |
| `binding_code_expires_at` | datetime nullable | 绑定码过期时间 |
| `last_authenticated_at` | datetime nullable | 最近认证成功 |
| `last_health_at` | datetime nullable | 最近健康检查 |
| `last_error_code` | varchar(64) nullable | 脱敏错误类别 |
| `last_error_message` | varchar(500) nullable | 脱敏摘要 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

约束和索引：

- 同一用户活动连接名唯一：部分唯一索引 `(owner_user_id, lower(name)) WHERE deleted_at IS NULL`。
- `(platform, state)`、`(owner_user_id, state)`。
- `binding_code_hash` 存在时唯一。

管理员列表只能读取平台、所有者、状态和脱敏错误，不能解密配置、绑定码或临时二维码。

### 4.2 `connector_outbox`

用于 Webhook/WebSocket/HTTP 连接器可靠投递和 cursor/ACK：

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK，同时作为单调 cursor |
| `connection_id` | integer | FK im_connections |
| `task_id` | integer | FK request_tasks |
| `payload_json` | text | 已脱敏任务投递包 |
| `delivery_state` | varchar(20) | `pending`, `delivered`, `acked`, `failed` |
| `attempt_count` | integer | 默认 0 |
| `available_at` | datetime | 下次可投递时间 |
| `acked_at` | datetime nullable | ACK 时间 |
| `last_error_code` | varchar(64) nullable | 脱敏错误 |
| `created_at` / `updated_at` | datetime | 非空 |

唯一约束 `(connection_id, task_id)`；索引 `(connection_id, id)`、`(delivery_state, available_at)`。

### 4.3 `inbound_receipts`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `connection_id` | integer | FK im_connections |
| `external_message_id` | varchar(255) | 非空 |
| `sender_fingerprint` | varchar(255) | 发送者脱敏标识 |
| `task_id` | integer nullable | 最终关联任务 |
| `payload_hash` | varchar(64) | 入站内容摘要，不保存凭据 |
| `result_code` | varchar(64) | accepted/duplicate/late/unbound 等 |
| `created_at` | datetime | 非空 |

唯一约束 `(connection_id, external_message_id)` 是全局消息幂等裁决。

### 4.4 `llm_configs`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `owner_user_id` | integer | FK users，非空 |
| `name` | varchar(100) | 非空 |
| `protocol` | enum | `openai_compatible` 或 `anthropic` |
| `base_url` | varchar(2048) | 非空，规范化后保存 |
| `real_model` | varchar(255) | 非空 |
| `secret_ciphertext` | text | API Key 认证加密 |
| `headers_ciphertext` | text nullable | 自定义 Header 整体认证加密 |
| `encryption_key_version` | integer | 非空 |
| `timeout_seconds` | integer | 非空，服务端范围校验 |
| `is_enabled` | boolean | 非空，默认 true |
| `last_tested_at` | datetime nullable | 最近连通性测试 |
| `last_test_result` | varchar(20) nullable | success/failed，不存响应原文 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

部分唯一索引 `(owner_user_id, lower(name)) WHERE deleted_at IS NULL`；索引 `(owner_user_id, is_enabled)`。

LLM Secret 和 Header 值永不出现在读取响应中。管理员可查看协议、Base URL 主机、真实模型和状态等治理元数据，但不能解密或代用配置。

## 5. Fake Model 与权限集合

### 5.1 `fake_models`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `scope` | enum | `system` 或 `private` |
| `owner_user_id` | integer nullable | private 必填；system 必须为空 |
| `model_id` | varchar(255) | 对外 `model` 字符串，非空 |
| `display_name` | varchar(255) nullable | 后台显示名 |
| `owned_by` | varchar(100) | 对外目录的 owned_by |
| `description` | text nullable | 非敏感说明 |
| `sort_order` | integer | 默认 0 |
| `is_enabled` | boolean | 默认 true |
| `created_by_user_id` | integer | FK users，用于审计 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

约束：

- CHECK：system 时 `owner_user_id IS NULL`；private 时 `owner_user_id IS NOT NULL`。
- 系统模型活动唯一：`model_id WHERE scope = 'system' AND deleted_at IS NULL`。
- 用户私有模型活动唯一：`(owner_user_id, model_id) WHERE scope = 'private' AND deleted_at IS NULL`。
- 索引 `(scope, is_enabled, sort_order)`、`(owner_user_id, is_enabled, sort_order)`。

普通用户查询的基础可见集合为所有启用系统模型与自己的启用私有模型。若私有模型和系统模型使用相同 `model_id`，服务层只保留私有模型作为该用户的可见项；创建/更新 API 会提示冲突，前端默认阻止产生遮蔽。其他普通用户永远不能查询或引用该私有模型。管理员治理列表可查看全部模型，但不能把私有模型转授给其他用户。

### 5.2 `model_groups`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `owner_user_id` | integer | FK users，非空 |
| `name` | varchar(100) | 非空 |
| `description` | varchar(500) nullable | 说明 |
| `is_enabled` | boolean | 默认 true |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

部分唯一索引 `(owner_user_id, lower(name)) WHERE deleted_at IS NULL`。

### 5.3 `model_group_items`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `model_group_id` | integer | FK model_groups，CASCADE |
| `fake_model_id` | integer | FK fake_models，RESTRICT |
| `created_at` | datetime | 非空 |

唯一约束 `(model_group_id, fake_model_id)`。Service 必须验证成员属于组所有者的可见模型集合；模型后来停用或删除时，自然从有效结果排除。

## 6. API Key

### 6.1 `api_keys`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `owner_user_id` | integer | FK users，非空 |
| `name` | varchar(100) | 非空 |
| `key_hash` | varchar(255) | 非空，唯一 |
| `key_prefix` | varchar(20) | 非空，用于列表识别 |
| `is_enabled` | boolean | 默认 true |
| `delivery_mode` | enum | web/im |
| `im_connection_id` | integer nullable | FK im_connections |
| `reply_strategy` | enum | human/llm/human_fallback_llm |
| `llm_config_id` | integer nullable | FK llm_configs |
| `human_timeout_seconds` | integer | 默认 300，CHECK 10-1800 |
| `model_group_id` | integer nullable | FK model_groups |
| `last_used_at` | datetime nullable | 最近使用 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

约束：

- delivery_mode=im 时 `im_connection_id` 必填；web 时必须为空。
- reply_strategy 使用真实 LLM 时 `llm_config_id` 必填；human 时允许为空。
- 引用的连接、LLM 配置和模型分组必须属于同一用户。
- 同一用户活动 Key 名称唯一。

索引：`owner_user_id`、`(owner_user_id, is_enabled)`、`im_connection_id`、`llm_config_id`、`model_group_id`。

### 6.2 `api_key_fake_models`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `api_key_id` | integer | FK api_keys，CASCADE |
| `fake_model_id` | integer | FK fake_models，RESTRICT |
| `created_at` | datetime | 非空 |

唯一约束 `(api_key_id, fake_model_id)`。

语义非常重要：

- 没有任何关联行表示“不直接筛选”，即允许模型分组预筛后的全部候选模型。
- 存在关联行表示只允许关联模型与候选集的交集。
- 不能使用“零行”表示禁止全部模型；若后续需要冻结 Key，应设置 `is_enabled=false`。
- Service 在同一事务中验证选中模型对 Key 所有者可见，且属于已绑定分组的候选集。

### 6.3 有效模型查询

有效集合公式：

```text
visible = enabled(system_models ∪ owner's_private_models)
grouped = visible                         if key.model_group_id is null
          visible ∩ enabled(group.items) otherwise
effective = grouped                       if key has no api_key_fake_models rows
            grouped ∩ key.selected_models otherwise
```

`GET /v1/models` 和三种推理入口必须复用同一个 Repository/Service 查询，不能分别实现导致“看得到但不能调用”或“看不到却能调用”。

## 7. 请求任务、事件和草稿

### 7.1 `request_tasks`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `public_id` | varchar(64) | 非空，唯一，供 UI/IM/协议日志使用 |
| `owner_user_id` | integer | FK users，非空 |
| `api_key_id` | integer | FK api_keys，非空 |
| `fake_model_id` | integer nullable | FK fake_models，删除时 SET NULL |
| `requested_model` | varchar(255) | Fake Model 字符串快照 |
| `protocol` | enum | 三种入站协议 |
| `raw_payload_json` | text | 调用方完整原始 JSON，非空 |
| `normalized_request_json` | text | 展示/转换用标准投影，非空 |
| `request_headers_json` | text nullable | 仅保留协议所需的非敏感 Header |
| `stream_requested` | boolean | 原请求是否流式 |
| `reply_strategy_snapshot` | enum | 创建时策略快照 |
| `delivery_mode_snapshot` | enum | 创建时入口快照 |
| `im_connection_id_snapshot` | integer nullable | 当时使用的连接 |
| `llm_config_id_snapshot` | integer nullable | 当时使用的上游配置 ID，仅内部 |
| `human_deadline_at` | datetime nullable | fallback/超时时间 |
| `state` | enum | 任务状态 |
| `version` | integer | 乐观版本，默认 1 |
| `response_payload_json` | text nullable | 完整规范化结果或协议快照 |
| `public_error_code` | varchar(64) nullable | 对外稳定错误码 |
| `slot_acquired_at` | datetime | 非空 |
| `slot_released_at` | datetime nullable | 名额幂等释放标记 |
| `response_started_at` | datetime nullable | 对外响应开始 |
| `completed_at` | datetime nullable | 终态时间 |
| `created_at` / `updated_at` | datetime | 非空 |

索引：

- `(owner_user_id, state, created_at)`：用户活动任务和工作台。
- `(api_key_id, created_at)`、`(requested_model, created_at)`。
- `(state, human_deadline_at)`：超时协调器。
- `slot_released_at`：一致性检查。

原始请求不经过字段白名单重建后再保存；必须序列化接收到的完整 JSON。Authorization、Cookie、API Key 等认证 Header 不进入 `request_headers_json`。

### 7.2 `task_events`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK，同时作为任务内稳定顺序的一部分 |
| `task_id` | integer | FK request_tasks，CASCADE |
| `event_type` | varchar(64) | created/delivered/reply_submitted/fallback/stream 等 |
| `actor_type` | varchar(30) | system/user/im/upstream/caller |
| `actor_user_id` | integer nullable | FK users |
| `payload_json` | text nullable | 脱敏事件详情 |
| `request_id` | varchar(64) nullable | 关联日志 |
| `created_at` | datetime | 非空 |

索引 `(task_id, id)`、`(event_type, created_at)`、`request_id`。任务事件只追加不更新。

晚到回复写 `reply_rejected_late` 事件，可保存内容哈希、来源和时间，不覆盖已接受响应，也不在管理员视图暴露用户完整草稿。

### 7.3 `task_drafts`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `task_id` | integer | FK request_tasks，CASCADE |
| `owner_user_id` | integer | FK users，冗余用于权限索引 |
| `source` | enum | manual/llm |
| `source_llm_config_id` | integer nullable | 生成草稿的配置 |
| `state` | enum | editing/submitted/discarded |
| `reasoning_text` | text nullable | 思考内容 |
| `tool_calls_json` | text | JSON 数组，默认 `[]` |
| `final_text` | text nullable | 最终文本 |
| `version` | integer | 乐观版本 |
| `created_at` / `updated_at` | datetime | 非空 |

索引 `(task_id, state, updated_at)`、`(owner_user_id, updated_at)`。提交任务时把选定草稿和任务结果在同一事务中标记，避免草稿已提交但任务仍可竞争。

## 8. Web 小助手

### 8.1 `assistant_sessions`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `owner_user_id` | integer | FK users，非空 |
| `title` | varchar(255) | 非空 |
| `llm_config_id` | integer nullable | FK llm_configs |
| `last_message_at` | datetime nullable | 排序 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 用户可删除 |

索引 `(owner_user_id, deleted_at, last_message_at)`。

### 8.2 `assistant_messages`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `session_id` | integer | FK assistant_sessions，CASCADE |
| `role` | enum | system/user/assistant |
| `content_json` | text | 消息内容 |
| `page_context_json` | text nullable | 经双重过滤的页面上下文 |
| `upstream_metadata_json` | text nullable | 脱敏用量、结束原因 |
| `created_at` | datetime | 非空 |

索引 `(session_id, id)`。Secret 过滤前的页面上下文不得落库。

## 9. 设置、审计与日志

### 9.1 `system_settings`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `key` | varchar(100) | PK |
| `value_json` | text | 非 Secret 配置 |
| `updated_by_user_id` | integer nullable | FK users |
| `updated_at` | datetime | 非空 |

必须包含 `schema_version` 和初始化种子版本。加密主密钥、管理员初始密码等 Secret 只来自环境变量，不进入本表。

### 9.2 `audit_logs`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `actor_user_id` | integer nullable | 系统动作可为空 |
| `action` | varchar(100) | 稳定动作代码 |
| `resource_type` | varchar(64) | 资源类型 |
| `resource_id` | varchar(64) nullable | 资源 ID 快照 |
| `owner_user_id` | integer nullable | 资源所有者 |
| `result` | varchar(20) | success/denied/failed |
| `request_id` | varchar(64) nullable | 关联请求 |
| `metadata_json` | text nullable | 脱敏变更摘要 |
| `created_at` | datetime | 非空 |

索引 `(created_at)`、`(actor_user_id, created_at)`、`(resource_type, resource_id)`、`owner_user_id`、`request_id`。审计记录不保存 Secret 变更前后值。

### 9.3 `app_logs`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `level` | varchar(10) | debug/info/warning/error |
| `event` | varchar(100) | 稳定事件代码 |
| `message` | varchar(1000) | 脱敏摘要 |
| `request_id` | varchar(64) nullable | 请求关联 |
| `user_id` / `task_id` / `api_key_id` / `connection_id` | integer nullable | 资源关联 |
| `context_json` | text nullable | 脱敏结构化上下文 |
| `created_at` | datetime | 非空 |

索引 `(created_at)`、`(level, created_at)` 和各关联 ID。数据库日志写入失败时输出同一结构到 stderr，不递归再次写数据库。

## 10. 关键事务

### 10.1 邀请码消费

注册事务使用等价条件更新：

```sql
UPDATE invitation_codes
SET used_count = used_count + 1,
    updated_at = :now
WHERE id = :id
  AND deleted_at IS NULL
  AND revoked_at IS NULL
  AND (expires_at IS NULL OR expires_at > :now)
  AND used_count < max_uses;
```

只有影响 1 行才创建用户并提交；0 行统一返回 400 `invalid_invitation`。用户名冲突或用户创建失败必须回滚消费次数。

### 10.2 原子占用任务名额

SQLite 写事务使用 `BEGIN IMMEDIATE`，先执行：

```sql
UPDATE users
SET active_task_count = active_task_count + 1,
    updated_at = :now
WHERE id = :owner_user_id
  AND is_active = 1
  AND active_task_count < 10;
```

影响 0 行返回协议兼容 429；影响 1 行后在同一事务创建 `request_tasks` 和 created 事件。任务创建失败则整个事务回滚。

### 10.3 幂等释放名额

终态推进事务先条件设置 `slot_released_at`：

```sql
UPDATE request_tasks
SET state = :terminal_state,
    slot_released_at = :now,
    completed_at = :now,
    version = version + 1
WHERE id = :task_id
  AND slot_released_at IS NULL;
```

只有影响 1 行时才把 `users.active_task_count` 减 1。重复完成、客户端断开重试或服务恢复不得重复扣减。

### 10.4 首个回复获胜

Web 或 IM 提交时，用任务 ID、所有者、期望状态和 version 条件更新：

```sql
UPDATE request_tasks
SET state = 'response_ready',
    response_payload_json = :response,
    version = version + 1,
    updated_at = :now
WHERE id = :task_id
  AND owner_user_id = :owner_user_id
  AND state = 'waiting_human'
  AND version = :expected_version;
```

成功后在同一事务追加事件并标记来源草稿；失败则读取最新状态，返回 409 并追加晚到审计，不覆盖结果。

### 10.5 fallback 唯一声明

超时协调器只允许 `waiting_human -> forwarding_llm` 条件更新成功的执行者调用真实 LLM。网络调用在事务提交后进行；失败再用独立事务进入 `failed` 并释放名额。

### 10.6 API Key 模型集合更新

更新 Key 的模型分组和直接模型集合时：

1. 在事务中读取 Key 所有者。
2. 验证分组属于该用户且有效。
3. 计算分组预筛后的候选模型。
4. 验证所有提交模型都在候选集中。
5. 原子替换 `api_key_fake_models` 关联。

空集合删除全部关联行，语义为允许全部候选模型。

## 11. 删除与引用规则

| 资源 | 删除行为 |
| --- | --- |
| 邀请码 | 软删除，立即不可消费，用户来源和审计保留。 |
| 用户 | 默认禁用；存在历史任务时不物理删除。 |
| IM 连接 | 先停止运行并清空凭据，再软删除；引用它的有效 Key 必须先改为 Web 或停用。 |
| LLM 配置 | 清空 Secret 后软删除；被有效转发 Key 引用时返回 409。 |
| Fake Model | 软删除并从目录立即消失；活动任务使用 requested_model 快照继续完成。 |
| 模型分组 | 被有效 Key 引用时返回 409。 |
| API Key | 立即停用、清空可撤销材料并软删除，历史任务保留 ID 和前缀。 |
| 任务/事件 | 普通用户不能删除；按未来保留策略由管理员任务清理。 |
| 草稿 | 用户可删除未提交草稿；已提交草稿随任务保留。 |
| 小助手会话 | 用户删除时级联删除消息或进入可配置保留队列。 |

## 12. 初始化流程

新数据库在单个初始化事务中：

1. 创建全部表和索引。
2. 写入当前 `schema_version`。
3. 校验 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`，创建管理员并只保存密码哈希。
4. 写入默认系统设置。
5. 从代码中的默认目录创建系统 Fake Model，并写入种子版本。
6. 提交后启动连接器运行时。

同一新 Schema 的重复启动必须幂等：已有管理员不被环境变量覆盖密码，管理员后来停用或删除的默认 Fake Model 不因普通重启被补回。种子只在全新数据库或显式受控初始化时执行。

## 13. 明确删除的旧结构

M2 直接删除并不再创建：

- `human_operators` 及用户一对一操作员关系。
- `model_routes` 及 API Key 的 `route_id`。
- 将真实供应商拆成全局 `llm_providers`、`llm_models` 的核心路由结构。
- 任何为旧字段补列、旧枚举映射或旧接口双写的启动逻辑。

旧开发数据库应备份后删除，由当前模型重新创建；不提供迁移脚本。

## 14. Schema 验收

- 空目录首次启动能自动创建数据库、管理员和默认系统 Fake Model。
- Schema 版本不一致时明确失败，不修改旧库。
- 邀请码并发消费不超过 `max_uses`。
- 同一用户多 Key 并发最多成功占用 10 个活动任务。
- 终态和异常路径只释放一次名额。
- Web/IM/fallback 竞争只有一个结果获胜。
- 其他用户无法查询或引用私有 Fake Model。
- 模型分组和 Key 选择只能缩小有效模型集合；空 Key 选择表示全部候选模型。
- `/v1/models` 和推理准入使用同一有效模型查询。
- Secret、Token、完整 Key 和密码不以明文落库或进入日志。

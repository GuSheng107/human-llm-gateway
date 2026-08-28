# Human LLM Gateway 数据库设计

> 文档状态：M1 目标 Schema 设计
>
> M2 将在一个完整提交中按本文件直接重建 SQLAlchemy 模型。项目不做历史数据库迁移，不为旧表或旧字段补兼容逻辑，也不允许新旧表或两套 metadata 共存。

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

### 2.4 Secret 加密契约

所有 `*_ciphertext` 字段（IM 连接配置、LLM Secret、自定义 Header）和加密自检 sentinel 使用同一套密码学契约，M2 不允许实现者自由发挥：

1. **主密钥格式**：`APP_SECRET` 是 32 字节 CSPRNG 随机值的 base64url 表示（无填充，43 个字符）。启动时校验：缺失、解码后不是 32 字节，或仍为 `.env.example` 默认值（`replace-with-a-long-random-secret`）时直接启动失败，不降级为警告。
2. **密钥派生**：使用 HKDF-SHA256 从 APP_SECRET 的 32 字节原始值派生当前版本加密密钥；HKDF info 固定为 `human-llm-gateway/secret-encryption/v1`，salt 为空。
3. **加密算法**：AES-256-GCM。每次加密生成全新 CSPRNG 96-bit nonce，同一 nonce 绝不复用。
4. **AAD（附加认证数据）**：固定为 `human-llm-gateway/<purpose>/v1`，按用途取值：
   - LLM Secret：`human-llm-gateway/llm-secret/v1`
   - LLM 自定义 Header：`human-llm-gateway/llm-headers/v1`
   - IM 连接配置：`human-llm-gateway/im-config/v1`
   - 加密自检 sentinel：`human-llm-gateway/sentinel/v1`

   AAD 绑定用途而不绑定 row ID：一个合法的 llm_secret 密文被复制到 im_config 字段后必须解密失败，同时不引入行级绑定带来的实现复杂度。
5. **envelope 格式**：文本格式，不做二进制拼接，避免字段宽度歧义：

   ```text
   hlg1.<key_version>.<nonce_b64url>.<ciphertext_and_tag_b64url>
   ```

   例如：

   ```text
   hlg1.1.k9T4xQ.AB3pZ…
   ```

   规则：`hlg1` 是 envelope 结构版本（固定前缀，非 `hlg1` 视为非法）；`<key_version>` 是十进制整数（当前为 1）；`<nonce_b64url>` 必须解码为 12 字节；`<ciphertext_and_tag_b64url>` 包含 AES-GCM 密文及其 16 字节认证 tag。四段之间以 `.` 分隔，格式不合法时按数据完整性错误处理，不得尝试猜测解析。
6. **key version**：当前固定为 1。M10 才利用该字段实现 key ring 与主密钥轮换；解密时遇到未知 `key_version` 按配置错误处理，不得静默跳过。
7. **列与 envelope 的一致性**：`encryption_key_version` / `config_key_version` 列必须与对应 envelope 中的 `<key_version>` 相同。两者不一致时按数据完整性错误处理（记录错误日志并拒绝该资源），不得自动选择其中一方——否则数据库列与密文会形成两份互相冲突的事实来源。
8. **解密权限**：Secret 明文永不通过任何 API 返回给管理员或其他用户。只有受信任的内部 Service 与 Connector Runtime 可以为执行已授权业务动作而临时解密（例如启动或 apply 连接需要读取用户 IM 凭据）。管理员触发生命周期治理动作（启动、停止、apply、检查、删除）时，服务端可以在内部使用该 Secret，但任何响应、日志、审计和错误都不得暴露明文或其可推导材料。

## 3. 身份与访问

### 3.1 `users`

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | integer | PK |
| `username` | varchar(64) | 非空，唯一；登录标识仅允许 ASCII 模式 `[a-z0-9][a-z0-9._-]{2,63}`，写入前 strip 并做 ASCII 小写归一。Unicode 展示名由 `display_name` 承担 |
| `display_name` | varchar(100) | 非空 |
| `password_hash` | varchar(255) | 非空，Argon2id 的 PHC 编码字符串（见下方密码哈希规则） |
| `must_change_password` | boolean | 非空，默认 false；置 true 时会话受限 |
| `role` | enum | 非空，默认 `user` |
| `is_active` | boolean | 非空，默认 true |
| `disabled_at` | datetime nullable | 管理员禁用时间 |
| `disabled_by_user_id` | integer nullable | FK users，执行禁用的管理员 |
| `active_task_count` | integer | 非空，默认 0，CHECK 0-10 |
| `registered_via_invitation_id` | integer nullable | FK invitation_codes，删除时 SET NULL |
| `last_login_at` | datetime nullable | 最近登录 |
| `created_at` / `updated_at` | datetime | 非空 |

索引：

- 唯一索引 `username`（普通唯一索引）。不使用 `lower(username)` 表达式索引：SQLite 内建 `lower()` 只处理 ASCII、不构成 Unicode casefold，语义在这里不可靠，换数据库也不可移植；由于写入前已强制 ASCII 小写，普通唯一索引即等价。
- `(role, is_active)` 管理筛选索引。

`active_task_count` 是并发准入的事务计数器，不通过扫描任务表决定第 11 个请求。后台可提供只读一致性检查，将计数与活动任务实际数量对比，但不能在普通请求中静默修正。禁用用户必须通过 UserService 的事务编排执行，不允许只翻转 `is_active`。

密码策略（注册、修改密码、管理员重置、CLI 创建和初始化环境变量统一适用）：

- 最少 15 个 Unicode code points，最大支持至少 128；不强制大小写、数字或特殊字符组合。
- 输入按 NFC 归一化后再哈希；允许空格和 Unicode 字符。
- 拒绝常见弱密码、与用户名相同或近似、以及明显部署默认词的 blocklist。
- 初始化环境变量 `ADMIN_PASSWORD` 不满足策略时启动失败，不得降级为警告后继续。

密码哈希规则（M2 必须按此实现，不允许保留当前 M0 的 scrypt 参数）：

- **算法固定为 Argon2id**。不使用 bcrypt（常见实现存在 72 字节输入截断，与“最长至少 128 个 code points”的密码策略冲突）；M0 代码使用的 scrypt `n=2**14, r=8, p=1` 低于 OWASP 推荐基线（scrypt 基线为 `N=2**17, r=8, p=1`），M2 直接切换到 Argon2id，不保留旧格式读取。
- **基线参数**：`m = 19456 KiB`（19 MiB）、`t = 2`、`p = 1`。
- **存储格式**：Argon2id 的 PHC 编码字符串，自身携带算法、版本和参数，例如：

  ```text
  $argon2id$v=19$m=19456,t=2,p=1$<salt_b64>$<hash_b64>
  ```

  `varchar(255)` 足够容纳该格式。验证时按 PHC 字符串中的参数执行，不依赖额外配置列。
- **参数升级**：未来可以提高 `m`/`t`。登录成功且发现存储的哈希参数低于当前策略时，在同一次认证流程中用已验证的明文重新哈希并更新该行，不要求全库迁移或强制用户改密；参数升级属于服务端行为，不写入审计明文。

`must_change_password` 只属于用户行，不写入 `system_settings`。置位规则：

| 场景 | 值 |
| --- | --- |
| 环境变量初始化的第一个管理员 | true |
| CLI 创建管理员且使用系统生成的临时密码 | true |
| CLI 创建管理员且操作者亲自输入密码 | false |
| 管理员重置普通用户为临时密码 | true |
| 用户成功主动修改密码 | false |

`must_change_password=true` 不阻止认证成功，否则用户无法调用改密接口；认证产生受限会话：仅允许 `GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/account/password`，其余 `/api/*` 返回 403。

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

明文邀请码只在创建响应展示一次。撤销负责立即禁止消费并继续显示；删除只允许作用于已撤销邀请码，采用软删除从普通列表隐藏，同时保留用户来源和审计证据。

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
| `retry_count` | integer | 连续自动重试次数，默认 0 |
| `next_retry_at` | datetime nullable | 下次自动重试时间 |
| `created_at` / `updated_at` / `deleted_at` | datetime | 软删除 |

约束和索引：

- 同一用户活动连接名唯一：部分唯一索引 `(owner_user_id, lower(name)) WHERE deleted_at IS NULL`。
- `(platform, state)`、`(owner_user_id, state)`。
- `binding_code_hash` 存在时唯一。

管理员列表只能读取平台、所有者、状态和脱敏错误，不能解密配置、绑定码或临时二维码。

普通网络错误且 `desired_running=true` 时更新 `retry_count` 和 `next_retry_at`，由运行时按带抖动的指数退避重连；认证失效时进入 `auth_required`、清空 `next_retry_at` 并等待所有者重新登录。手动停止会设置 `desired_running=false`，不得被后台重试重新拉起。

`retry_count` 定义为“连续自动重试次数”，重置采用稳定成功语义：

- 进入 `online` 时立即清空 `next_retry_at`。
- 只有连续保持健康 60 秒后才把 `retry_count` 重置为 0；期间再次断线则继承上一轮退避级别继续退避，不从头开始。
- 这样既避免长期正常后每次故障都从最短退避重来，也避免网络抖动时因瞬时连接成功形成紧密重连循环。

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
| `response_public_id` | varchar(64) nullable | OpenAI Responses 对外响应 ID，唯一；在任务创建事务中生成（仅 `protocol = openai_responses` 非空，条件约束保证），格式为 `resp_` + 32 位小写 hex（CSPRNG 生成） |
| `previous_task_id` | integer nullable | FK request_tasks，同一 API Key 的历史响应链 |
| `owner_user_id` | integer | FK users，非空 |
| `api_key_id` | integer | FK api_keys，非空，ON DELETE RESTRICT |
| `api_key_prefix_snapshot` | varchar(20) | 创建任务时 Key 前缀快照 |
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
| `llm_config_snapshot_json` | text nullable | 名称、协议、规范化 Base URL、真实模型等非敏感快照 |
| `human_deadline_at` | datetime nullable | fallback/超时时间 |
| `state` | enum | 任务状态 |
| `version` | integer | 乐观版本，默认 1 |
| `response_payload_json` | text nullable | 完整规范化 ReplyDraft 结果 |
| `public_error_code` | varchar(64) nullable | 对外稳定错误码 |
| `cancel_reason_code` | varchar(64) nullable | 内部取消类别，不直接回显 |
| `slot_acquired_at` | datetime | 非空 |
| `slot_released_at` | datetime nullable | 名额幂等释放标记 |
| `response_started_at` | datetime nullable | 对外响应开始 |
| `completed_at` | datetime nullable | 终态时间 |
| `created_at` / `updated_at` | datetime | 非空 |

索引：

- `(owner_user_id, state, created_at)`：用户活动任务和工作台。
- `(api_key_id, created_at)`、`(requested_model, created_at)`。
- `response_public_id` 唯一索引、`previous_task_id` 历史链索引。
- `(state, human_deadline_at)`：超时协调器。
- `slot_released_at`：一致性检查。

原始请求不经过字段白名单重建后再保存；必须序列化接收到的完整 JSON。Authorization、Cookie、API Key 等认证 Header 不进入 `request_headers_json`。

`previous_response_id` 原始值留在 `raw_payload_json`，解析成功后写入 `previous_task_id`。引用必须属于同一 `api_key_id` 且指向已完成响应；规范化请求保存等价展开后的上下文。历史清理必须保留仍被引用任务的最小请求/回复快照，不能留下悬空链。

`api_key_id` 物理清理依赖 `ON DELETE RESTRICT` 保护；API Key 的“删除”采用软删除，历史任务的 FK 始终指向存在的 Key 行。`api_key_prefix_snapshot` 仅作为历史展示快照，不替代 FK。物理清理必须由未来的 retention job 按完整依赖关系执行，平时不把历史任务的 `api_key_id` 设为 NULL。

`response_public_id` 使用 `resp_` + 32 位小写 hex（CSPRNG，例如 `resp_a10c46f728e24da0970ba9e7189f429d`）。这是网关在自己命名空间内签发的协议兼容 ID，不是冒充真实 OpenAI response ID；任务内部仍以 integer PK 为主键。生成时机是任务创建事务，而不是响应成功之后：发送第一个 Responses 响应事件（包括 `response.created` 和失败终态 `response.failed`）之前该 ID 必须已经持久化，全程沿用同一 ID。数据库条件约束保证 `protocol = openai_responses` 的任务 `response_public_id` 非空，Chat 和 Anthropic 任务保持为空。只有 COMPLETED 状态的响应可被后续 `previous_response_id` 引用；失败或取消的响应保留 ID，但不能成为历史链父节点。

`response_payload_json` 是 IM DSL、Web 编辑器、LLM 草稿和三协议渲染器共享的唯一回复结构：

```json
{
  "reasoning": "可选文本",
  "tool_calls": [
    {"id": "call_01", "name": "lookup", "arguments": {"id": 1}}
  ],
  "final_text": "最终回复"
}
```

协议专有 ID、SSE 序号和 finish reason 由渲染器生成，不反向改变该结构。首个提交成功后没有撤销或覆盖事务。

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
| `page_route` | varchar(255) nullable | 发送时当前路由 |
| `page_feature` | varchar(100) nullable | 发送时 feature 代码 |
| `context_version` | integer nullable | 页面上下文版本，按 feature 独立递增 |
| `upstream_metadata_json` | text nullable | 脱敏用量、结束原因 |
| `created_at` | datetime | 非空 |

索引 `(session_id, id)`。Secret 过滤前的页面上下文不得落库。每条消息只保存发送时当前浏览器标签页的上下文快照；页面切换不修改历史消息，也不把旧页面上下文自动合并进新消息。

组合约束（CHECK 或 Service 不变量，写入前校验）：`page_context_json IS NOT NULL` 时 `page_feature` 与 `context_version` 必须非空；`page_context_json IS NULL` 时二者必须为空。保证有上下文快照的消息一定能按 `page_feature` + `context_version` 解释，不出现无法归档的坏数据。

`page_feature` 是 feature 在 `PageContextRegistry` 中注册的稳定标识（如 `api_keys`），`context_version` 是该 feature 自己递增的整数。feature 的 context serializer 是版本 owner，序列化结构或字段语义变化必须 bump 版本号；多个 feature 各自维护版本，互相独立。历史消息不迁移、不回写，新消息使用新版本。读取时按 `page_feature` + `context_version` 解释上下文；不再支持时按历史 opaque JSON 展示即可，不得为了旧版本重新构造字段。

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

索引 `(created_at)`、`(actor_user_id, created_at)`、`(resource_type, resource_id)`、`owner_user_id`、`request_id`。`action` 必须来自集中枚举。`metadata_json` 只允许字段名、非敏感状态、计数和关联 ID，不保存请求正文、业务内容、Secret 变更前后值或任何凭据恢复材料；管理员能够看到发生过“凭据已轮换”属于预期审计能力。

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

### 10.7 禁用用户

管理员禁用用户由 UserService 编排，不允许直接修改单列：

1. 在短事务中条件更新 `users.is_active=false`，记录操作者和禁用时间。
2. 撤销全部未失效 `auth_sessions`，停用该用户全部 API Key。
3. 把所有尚未终止的任务条件推进到 `cancelled`，写入内部 `user_disabled` 原因。
4. 对每个 `slot_released_at IS NULL` 的任务幂等设置释放时间，并把 `active_task_count` 扣减到 0。
5. 提交后通知本进程和其他实例取消等待、上游请求与流式输出；对外仅产生通用协议错误。

事务和进程通知之间发生崩溃时，启动恢复任务必须根据数据库终态补发取消信号，但不能重复扣减名额。最后一个有效管理员不能被禁用。

### 10.8 历史响应引用

解析 `previous_response_id` 时，在同一只读快照中校验 response ID、`api_key_id`、完成状态和创建时间，再把 `previous_task_id` 与新任务一起写入。其他 Key 的响应统一按无效引用返回，不能暴露其是否存在。

网关层采用三重硬性保护，三项任一超限直接整请求返回 400 `context_length_exceeded`，不部分截断：

| 限制 | 默认值 | 含义 |
| --- | --- | --- |
| `max_chain_depth` | 20 | 沿 `previous_task_id` 可追溯的历史祖先节点数量上限；当前请求不计入。 |
| `max_expanded_items` | 512 | 展开后规范化顶级上下文条目累计上限：一条 message、一个 tool call、一个 reasoning 项等各计 1 条；message 内的多个 content block 合并计 1 条。三种协议使用同一个预算函数，不做协议各自计数。 |
| `max_expanded_context_bytes` | 2 MiB | 规范化展开 JSON 的 compact UTF-8 字节上限：`ensure_ascii=false`、无缩进，序列化参数固定，避免不同实现对同一上下文得出不同字节数。 |

Fake Model 与真实上游模型解耦，领域层不存在通用 tokenizer，因此网关不在准入阶段估算 token。M7 在协议 adapter 中处理真实模型的 token 限制：有可靠本地 tokenizer 就预检；没有就正常转发并把上游明确的上下文超限错误映射为 400 `context_length_exceeded`。每次推理不强制调用额外远程 `count_tokens`。

规范化展开必须有唯一语义：链 A→B→C 时 A 不会被重复展开；展开过程按时间顺序串联历史请求与回复，并保留原始 `previous_response_id` 以便审计和回放。

### 10.9 加密自检 sentinel

初始化阶段在数据库写入一个固定明文的认证加密 ciphertext（位置和键名由 `system_settings` 提供，例如 `key=encryption_sentinel`），并在每次启动的 `/readyz` 流程中解密验证。sentinel 使用 §2.4 的统一加密契约，能同时发现“密钥派生/算法实现漂移”和“数据库恢复了但 `APP_SECRET` 用错”两类灾难性配置错误；哨兵本身不携带业务数据。

### 10.10 调用方断开取消

外部调用方连接在任务终态前断开时，按“首个合法转换获胜”规则条件更新任务：

```sql
UPDATE request_tasks
SET state = 'cancelled',
    cancel_reason_code = 'caller_disconnected',
    slot_released_at = :now,
    completed_at = :now,
    version = version + 1
WHERE id = :task_id
  AND state NOT IN ('completed', 'failed', 'timed_out', 'cancelled')
  AND slot_released_at IS NULL;
```

影响 1 行才扣减 `users.active_task_count`；影响 0 行说明任务已进入终态（例如 COMPLETED 与断开的竞争），保持原结果不变。断开检测来自传输层取消回调；进程崩溃后由启动恢复任务按数据库终态补发取消，不重复释放名额。

## 11. 删除与引用规则

| 资源 | 删除行为 |
| --- | --- |
| 邀请码 | 先撤销使其立即不可消费；删除只对已撤销记录执行软删除，用户来源和审计保留。 |
| 用户 | 禁用时撤销会话和 Key、终止活动任务并释放名额；存在历史任务时不物理删除。 |
| IM 连接 | 先停止运行并清空凭据，再软删除；引用它的有效 Key 必须先改为 Web 或停用。 |
| LLM 配置 | 清空 Secret 后软删除；被有效转发 Key 或活动任务引用时返回 409，历史任务保留非敏感快照。 |
| Fake Model | 软删除并从目录立即消失；活动任务使用 requested_model 快照继续完成。 |
| 模型分组 | 被有效 Key 引用时返回 409。 |
| API Key | 立即停用并软删除，阻止新请求；已准入任务按快照继续，历史任务保留 ID 和前缀。 |
| 任务/事件 | 普通用户不能删除；未来清理必须保留被 `previous_task_id` 引用的最小上下文快照。 |
| 草稿 | 用户可删除未提交草稿；已提交草稿随任务保留。 |
| 小助手会话 | 用户删除时级联删除消息或进入可配置保留队列。 |

## 12. 初始化流程

新数据库在单个初始化事务中：

1. 创建全部表和索引。
2. 写入当前 `schema_version` 和加密自检 sentinel。
3. 按 §3.1 规则校验 `ADMIN_USERNAME`（ASCII 模式与小写归一）并按 §3.1 密码策略校验 `ADMIN_PASSWORD`，不满足时启动失败；创建管理员时 `must_change_password=true`，只保存密码哈希。
4. 写入默认系统设置。
5. 从代码中的默认目录创建系统 Fake Model，并写入种子版本。
6. 提交后启动连接器运行时。

同一新 Schema 的重复启动必须幂等：已有管理员不被环境变量覆盖密码，管理员后来停用或删除的默认 Fake Model 不因普通重启被补回。种子只在全新数据库或显式受控初始化时执行。后续管理员只能通过受控 CLI 创建：`uv run python -m app.cli admin create --username <name> --display-name <name>`。CLI 交互式创建使用 `getpass` 隐藏输入并要求二次确认，禁止 `--password` 明文参数；自动化场景使用 `--password-stdin --yes` 从 stdin 读取，或使用 `--generate-password --yes` 由系统生成临时密码。`--generate-password` 与 `--password-stdin` 互斥；生成密码使用 CSPRNG 且满足密码策略，明文只在 stdout 显示一次，不写入日志或审计。CLI 复用同一 UserService、密码策略、审计机制和应用配置（数据库路径、主密钥），不允许另搞一套；使用系统生成临时密码时 `must_change_password=true`。操作系统级数据库与部署访问权即该 CLI 的授权边界。CLI 记录审计并拒绝产生“零个有效管理员”。

## 13. 明确删除的旧结构

M2 的单个目标提交直接删除并不再创建：

- `human_operators` 及用户一对一操作员关系。
- `model_routes` 及 API Key 的 `route_id`。
- 将真实供应商拆成全局 `llm_providers`、`llm_models` 的核心路由结构。
- 任何为旧字段补列、旧枚举映射或旧接口双写的启动逻辑。

旧开发数据库应备份后删除，由当前模型重新创建；不提供迁移脚本。M2 不允许把旧表与目标表一起提交，不允许临时双写、兼容查询、旧 API 代理或包含两套 metadata 的启动模式。

## 14. 备份与恢复约束

- SQLite 运行中备份使用在线 backup API 或经过验证的 `VACUUM INTO`；启用 WAL 时禁止只复制主数据库文件。
- 备份包必须同时包含数据库、加密主密钥的独立安全备份和 Schema 版本说明，但三者不能以明文放在同一不受控位置。
- 恢复流程先在隔离目录校验完整性和 Schema 版本，再停止写流量、替换数据库并执行 `/readyz` 检查。
- M10 必须提供自动备份、保留期、恢复演练和失败告警说明；没有完成恢复演练不能进入 M11 发布验收。

## 15. Schema 验收

- 空目录首次启动能自动创建数据库、管理员和默认系统 Fake Model。
- Schema 版本不一致时明确失败，不修改旧库。
- 邀请码并发消费不超过 `max_uses`。
- 同一用户多 Key 并发最多成功占用 10 个活动任务。
- 终态和异常路径只释放一次名额。
- 禁用用户会终止全部活动任务，恢复流程不会重复释放名额。
- Web/IM/fallback 竞争只有一个结果获胜。
- 其他用户无法查询或引用私有 Fake Model。
- 模型分组和 Key 选择只能缩小有效模型集合；空 Key 选择表示全部候选模型。
- `/v1/models` 和推理准入使用同一有效模型查询。
- `previous_response_id` 只能引用同一 API Key 的完成响应，历史链不会因清理产生悬空引用。
- `previous_response_id` 展开遵守链深、条目数和字节数三重上限，超限整请求 400 且不部分截断。
- IM DSL 与 Web 回复写入同一个 ReplyDraft JSON Schema，首个提交后无撤销路径。
- Secret、Token、完整 Key 和密码不以明文落库或进入日志。
- 初始化环境变量密码不符合策略时启动失败；首个管理员 `must_change_password=true` 且登录后处于受限会话。
- M2 目标 Schema 中不存在旧表或新旧共存元数据。

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

// ---------------------------------------------------------------------------
// IM 连接（docs/API_CONTRACT.md §5）
// ---------------------------------------------------------------------------

export interface PlatformFieldSchema {
  name: string;
  label: string;
  type: "string" | "url" | "int" | "boolean";
  required: boolean;
  secret: boolean;
  description: string;
  /** "gateway_token"：网关自签接入 Token，由服务端自动生成，不允许手填。 */
  credential_kind?: string;
  auto_generate?: boolean;
}

export interface PlatformSpec {
  code: string;
  label: string;
  description: string;
  kind: "server" | "client";
  supports_delivery: boolean;
  supports_login: boolean;
  requires_binding: boolean;
  binding_command: string | null;
  config_schema: PlatformFieldSchema[];
}

export interface ImConnection {
  id: string;
  name: string;
  platform: string;
  platform_label: string;
  state: string;
  desired_running: boolean;
  bound: boolean;
  owner_user_id: string | null;
  owner_username: string | null;
  config: Record<string, unknown>;
  /** 仅创建响应一次性返回的网关自签 Token 明文；只保存在 React 临时状态。 */
  generated_tokens?: Record<string, string> | null;
  last_error_code: string | null;
  last_error_message: string | null;
  retry_count: number;
  next_retry_at: string | null;
  last_health_at: string | null;
  created_at: string;
}

export interface ConnectionHealth {
  state: string;
  desired_running: boolean;
  retry_count: number;
  next_retry_at: string | null;
  last_authenticated_at: string | null;
  last_health_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  runtime: { running: boolean; [key: string]: unknown };
}

export interface ConnectionCheckItem extends ConnectionHealth {
  id: string;
  name: string;
  platform: string;
  platform_label: string;
  owner_username: string | null;
  bound: boolean;
  abnormal: boolean;
  auto_disabled: boolean;
}

export interface BindingCode {
  binding_code: string;
  expires_at: string | null;
}

export interface BindingStatus {
  bound: boolean;
  binding_pending: boolean;
  binding_expires_at: string | null;
}

// ---------------------------------------------------------------------------
// Fake Model 与模型分组（docs/API_CONTRACT.md §7）
// ---------------------------------------------------------------------------

export interface FakeModel {
  id: string;
  scope: "system" | "private";
  owner_user_id: string | null;
  model_id: string;
  display_name: string | null;
  owned_by: string;
  description: string | null;
  sort_order: number;
  is_enabled: boolean;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  cached_input_price_per_million: number | null;
  cached_write_price_per_million: number | null;
  context_window: number | null;
  max_output_tokens: number | null;
  capabilities: string[];
  billing_tier: string;
  endpoint_types: string[];
  logo_url: string | null;
  tags: string[];
  created_at: string;
}

export interface ModelGroup {
  id: string;
  owner_user_id: string;
  name: string;
  description: string | null;
  is_enabled: boolean;
  model_ids: string[];
  is_public: boolean;
  can_manage: boolean;
  can_assign_model: boolean;
  created_at: string;
}

// ---------------------------------------------------------------------------
// API Key（docs/API_CONTRACT.md §8）
// ---------------------------------------------------------------------------

export type DeliveryMode = "web" | "im";
export type ReplyStrategy = "human" | "llm" | "human_fallback_llm";

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  is_enabled: boolean;
  delivery_mode: DeliveryMode;
  im_connection_id: string | null;
  reply_strategy: ReplyStrategy;
  llm_config_id: string | null;
  human_timeout_seconds: number;
  model_group_id: string | null;
  fake_model_ids: string[];
  fake_model_names: string[];
  last_used_at: string | null;
  created_at: string;
  owner_user_id: string | null;
  owner_username: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  plaintext: string;
}

// ---------------------------------------------------------------------------
// 任务工作台（docs/API_CONTRACT.md §9）
// ---------------------------------------------------------------------------

export type TaskState =
  | "received"
  | "waiting_human"
  | "forwarding_llm"
  | "response_ready"
  | "responding"
  | "completed"
  | "failed"
  | "timed_out"
  | "cancelled";

export type TaskProtocol =
  | "openai_chat"
  | "openai_responses"
  | "anthropic_messages";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolDefinition {
  name: string;
  description: string | null;
  parameters: Record<string, unknown>;
  source_type: string;
}

export interface ReplyDraft {
  reasoning: string | null;
  tool_calls: ToolCall[];
  final_text: string | null;
}

export interface TaskDraft extends ReplyDraft {
  id: string;
  source: "manual" | "llm";
  state: "editing" | "submitted" | "discarded";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TaskEvent {
  id: string;
  event_type: string;
  actor_type: string;
  actor_user_id: string | null;
  request_id: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskItem {
  id: string;
  public_id: string;
  requested_model: string;
  fake_model_name: string;
  protocol: TaskProtocol;
  state: TaskState;
  reply_strategy: string;
  delivery_mode: string;
  api_key_prefix: string;
  api_key_name: string;
  display_name: string;
  stream_requested: boolean;
  has_tools: boolean;
  /** 提示词尾部预览（Agent 提示词的提问在末尾）。 */
  prompt_preview: string;
  response_id: string | null;
  human_deadline_at: string | null;
  created_at: string;
  completed_at: string | null;
  owner_user_id: string | null;
  owner_username: string | null;
}

export interface TaskDetail extends TaskItem {
  origin_trace_id: string | null;
  is_owner: boolean;
  can_edit: boolean;
  prompt_text: string;
  tool_definitions: ToolDefinition[];
  raw_request: Record<string, unknown> | null;
  previous_task_id: string | null;
  drafts: TaskDraft[];
  active_draft_id: string | null;
  result_draft: ReplyDraft | null;
  public_error_code: string | null;
  cancel_reason_code: string | null;
  events: TaskEvent[];
  events_total: number;
}

export interface ReplyResult {
  accepted: boolean;
  task_id: string;
  state: TaskState;
}

// ---------------------------------------------------------------------------
// LLM 配置（docs/API_CONTRACT.md §6）
// ---------------------------------------------------------------------------

export interface LlmConfig {
  id: string;
  name: string;
  protocol: "openai_chat" | "openai_responses" | "anthropic_messages";
  base_url: string;
  base_url_host: string;
  real_model: string;
  timeout_seconds: number;
  is_enabled: boolean;
  api_key_set: boolean;
  default_temperature: number | null;
  default_top_p: number | null;
  default_top_k: number | null;
  max_output_tokens: number | null;
  context_window_input: number | null;
  context_window_output: number | null;
  max_tool_call_rounds: number;
  supports_image_input: boolean;
  thinking_mode: "model_default" | "enabled" | "disabled";
  thinking_level: "low" | "medium" | "high" | null;
  extra_body: Record<string, unknown>;
  last_tested_at: string | null;
  last_test_result: string | null;
  created_at: string;
  updated_at: string;
  owner_user_id: string | null;
  owner_username: string | null;
}

export interface LlmConfigTestResult {
  success: boolean;
  reason_code: string;
  detail: string;
  http_status: number | null;
  last_tested_at: string;
}

// ---------------------------------------------------------------------------
// Web 小助手（docs/API_CONTRACT.md §10）
// ---------------------------------------------------------------------------

export type AssistantRole = "system" | "user" | "assistant" | "summary";

export interface AssistantToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface AssistantUnsavedEdit {
  reasoning: string | null;
  final_text: string | null;
  tool_calls: AssistantToolCall[];
}

export interface AssistantPageContext {
  route: string;
  feature: string;
  resource: Record<string, string>;
  unsaved_edit: AssistantUnsavedEdit | null;
  context_version: number;
}

export type AssistantMessageKind = "normal" | "summary";

export interface AssistantMessage {
  id: string;
  role: AssistantRole;
  kind: AssistantMessageKind;
  text: string;
  page_context: AssistantPageContext | null;
  upstream_metadata: Record<string, unknown> | null;
  trace_id?: string | null;
  error_code?: string | null;
  created_at: string;
}

export interface AssistantSession {
  id: string;
  title: string;
  llm_config_id: string | null;
  last_message_at: string | null;
  created_at: string;
}

export interface AssistantSessionUsage {
  estimated_tokens: number;
  limit_tokens: number;
  ratio: number;
  message_count: number;
  compressing: boolean;
}

export interface AssistantSessionDetail extends AssistantSession {
  messages: AssistantMessage[];
  usage: AssistantSessionUsage;
}

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
}

export interface PlatformSpec {
  code: string;
  label: string;
  description: string;
  kind: "server" | "client";
  supports_delivery: boolean;
  supports_login: boolean;
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

export interface BindingCode {
  binding_code: string;
  expires_at: string;
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
  created_at: string;
}

export interface ModelGroup {
  id: string;
  owner_user_id: string;
  name: string;
  description: string | null;
  is_enabled: boolean;
  model_ids: string[];
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

export type UserRole = "admin" | "user";

export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  role: UserRole;
}

export interface LoginResponse extends CurrentUser {
  access_token: string;
  token_type: string;
}

export interface PlatformField {
  key: string;
  label: string;
  kind: "text" | "url" | "number" | "json" | "password";
  required: boolean;
  secret: boolean;
  placeholder: string;
  default: string | number | boolean | null;
}

export interface PlatformDefinition {
  id: string;
  label: string;
  description: string;
  capabilities: string[];
  enabled: boolean;
  fields: PlatformField[];
}

export interface IMConnection {
  id: number;
  name: string;
  platform: string;
  status: "disabled" | "offline" | "connecting" | "online" | "error";
  binding_status: "unbound" | "binding" | "bound" | "expired";
  owner_id: number | null;
  owner_name: string;
  bound_user_id: string;
  bound_conversation_id: string;
  last_seen_at: string | null;
  last_error: string;
  created_at: string;
}

export interface ConnectionCreated extends IMConnection {
  setup: Record<string, unknown>;
}

export interface HealthSnapshot {
  status: string;
  platform?: string;
  login_state?: string;
  state?: string;
  qr?: string;
  error?: string;
  authenticated?: boolean;
  connected?: number;
  cursor?: string;
}

export interface BindingSnapshot {
  connection_id: number;
  code: string;
  command: string;
  expires_at: string;
}

export type ViewId =
  | "console"
  | "connections"
  | "api"
  | "llm"
  | "reply"
  | "settings"
  | "users";

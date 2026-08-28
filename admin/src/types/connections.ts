export type ConnectorStatus =
  | "offline"
  | "connecting"
  | "online"
  | "error"
  | "stopped"
  | "pending_restart";

export type BindingStatus = "unbound" | "waiting" | "bound" | "expired" | "locked";

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

export interface AllowedAction {
  start?: boolean;
  stop?: boolean;
  apply?: boolean;
  regenerate_binding?: boolean;
  view_logs?: boolean;
  delete?: boolean;
  [key: string]: boolean | undefined;
}

export interface IMConnection {
  id: number;
  name: string;
  platform: string;
  status: ConnectorStatus;
  binding_status: BindingStatus;
  owner_id: number;
  owner_name: string;
  bound_user_id: string;
  bound_conversation_id: string;
  last_seen_at: string | null;
  last_error: string;
  error_code: string;
  restart_required: boolean;
  config_version: number;
  applied_version: number;
  consecutive_failures: number;
  allowed_actions: string[];
  updated_at: string;
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
  [key: string]: unknown;
}

export interface BindingSnapshot {
  connection_id: number;
  code: string;
  command: string;
  expires_at: string;
}

export interface BindingStatusSnapshot {
  binding_status: BindingStatus;
  remaining_seconds: number | null;
  locked: boolean;
  locked_until_seconds: number | null;
  failed_attempts: number;
  max_attempts: number;
}

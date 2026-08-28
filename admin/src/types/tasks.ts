import type { RouteMode } from "./llm";

export type TaskStatus =
  | "received"
  | "authenticated"
  | "routed"
  | "human_waiting"
  | "llm_streaming"
  | "pseudo_streaming"
  | "tool_pending"
  | "completed"
  | "timeout"
  | "cancelled"
  | "failed";

export interface TaskEvent {
  sequence: number;
  kind: "reasoning" | "tool_call" | "final";
  content: string;
  tool_name: string | null;
  tool_args: string | null;
  source: "im" | "web" | "llm";
  external_message_id: string | null;
  created_at: string;
}

export interface TaskSummary {
  id: string;
  api_key_id: number;
  protocol: string;
  model: string;
  status: TaskStatus;
  error: string | null;
  created_at: string;
  route_mode?: RouteMode;
  model_name?: string;
  owner_id?: number;
}

export interface TaskDetail extends TaskSummary {
  events: TaskEvent[];
}

export interface AuditLogItem {
  id: number;
  action: string;
  subject_type: string;
  subject_id: string;
  actor: string;
  detail: string;
  created_at: string;
}

export interface AppLogItem {
  id: number;
  level: string;
  logger: string;
  message: string;
  detail: string;
  created_at: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

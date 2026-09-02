import { api } from "./client";

export interface AuditLogItem {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  actor_user_id: string | null;
  actor_username: string | null;
  owner_user_id: string | null;
  result: string;
  request_id: string | null;
  fields: string[];
  created_at: string;
}

export interface AppLogItem {
  id: string;
  level: string;
  event: string;
  message: string;
  request_id: string | null;
  logger?: string | null;
  user_id: string | null;
  username: string | null;
  task_id: string | null;
  api_key_id: string | null;
  connection_id: string | null;
  created_at: string;
  context?: Record<string, unknown> | null;
}

export interface DashboardStats {
  total_users: number;
  active_users: number;
  total_tasks: number;
  active_tasks: number;
  total_api_keys: number;
  active_models: number;
}

export interface DashboardRecentTask {
  id: string;
  public_id: string;
  model: string;
  protocol: string;
  state: string;
  created_at: string;
  human_deadline_at: string | null;
}

export interface DashboardDailyTask {
  date: string;
  count: number;
}

export interface DashboardProtocolCount {
  protocol: string;
  count: number;
}

export interface DashboardData {
  stats: DashboardStats;
  recent_tasks: DashboardRecentTask[];
  daily_tasks: DashboardDailyTask[];
  protocol_counts: DashboardProtocolCount[];
}

export interface AuditLogQuery {
  page: number;
  page_size?: number;
  actor_user_id?: number;
  resource_type?: string;
  action?: string;
  owner_user_id?: number;
  hours?: number;
}

export interface AppLogQuery {
  page: number;
  page_size?: number;
  level?: string;
  event?: string;
  user_id?: number;
  task_id?: number;
  api_key_id?: number;
  connection_id?: number;
  request_id?: string;
  hours?: number;
  with_context?: boolean;
}

export function listAuditLogs(query: AuditLogQuery): Promise<{
  items: AuditLogItem[];
  page: number;
  page_size: number;
  total: number;
}> {
  const params = new URLSearchParams({
    page: String(query.page),
    page_size: String(query.page_size ?? 20),
  });
  if (query.actor_user_id) params.set("actor_user_id", String(query.actor_user_id));
  if (query.resource_type) params.set("resource_type", query.resource_type);
  if (query.action) params.set("action", query.action);
  if (query.owner_user_id) params.set("owner_user_id", String(query.owner_user_id));
  if (query.hours) params.set("hours", String(query.hours));
  return api(`/api/audit-logs?${params}`);
}

export function listAppLogs(query: AppLogQuery): Promise<{
  items: AppLogItem[];
  page: number;
  page_size: number;
  total: number;
}> {
  const params = new URLSearchParams({
    page: String(query.page),
    page_size: String(query.page_size ?? 20),
  });
  if (query.level) params.set("level", query.level);
  if (query.event) params.set("event", query.event);
  if (query.user_id) params.set("user_id", String(query.user_id));
  if (query.task_id) params.set("task_id", String(query.task_id));
  if (query.api_key_id) params.set("api_key_id", String(query.api_key_id));
  if (query.connection_id) params.set("connection_id", String(query.connection_id));
  if (query.request_id) params.set("request_id", query.request_id);
  if (query.hours) params.set("hours", String(query.hours));
  if (query.with_context) params.set("with_context", "true");
  return api(`/api/app-logs?${params}`);
}

export function getDashboard(): Promise<DashboardData> {
  return api("/api/dashboard");
}

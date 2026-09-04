import { api } from "./client";

/**
 * 统一日志查询：合并审计与应用日志，按 trace_id 串联。
 * 后端按调用者角色做数据可见性裁剪；普通用户只能看到自己相关的行。
 */
export interface LogItem {
  id: string;
  kind: "audit" | "app";
  level: string;
  event: string;
  message: string;
  username: string | null;
  user_id: string | null;
  request_id: string | null;
  task_id: string | null;
  api_key_id: string | null;
  connection_id: string | null;
  created_at: string;
}

export interface LogPage {
  items: LogItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface LogQuery {
  page: number;
  page_size?: number;
  trace_id?: string;
  event?: string;
  hours?: number;
}

export function listLogs(query: LogQuery): Promise<LogPage> {
  const params = new URLSearchParams({
    page: String(query.page),
    page_size: String(query.page_size ?? 20),
  });
  if (query.trace_id) params.set("trace_id", query.trace_id);
  if (query.event) params.set("event", query.event);
  if (query.hours) params.set("hours", String(query.hours));
  return api(`/api/logs?${params}`);
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

export function getDashboard(): Promise<DashboardData> {
  return api("/api/dashboard");
}

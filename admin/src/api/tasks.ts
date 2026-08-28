import { api } from "./client";
import type { AppLogItem, AuditLogItem, Paged, TaskDetail, TaskSummary } from "../types/tasks";

export async function listTasks(params: {
  page?: number;
  page_size?: number;
  status?: string;
  api_key_id?: number;
}): Promise<Paged<TaskSummary>> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.status) query.set("status", params.status);
  if (params.api_key_id) query.set("api_key_id", String(params.api_key_id));
  return api<Paged<TaskSummary>>(`/api/tasks?${query.toString()}`);
}

export async function getTask(id: string): Promise<TaskDetail> {
  return api<TaskDetail>(`/api/tasks/${id}`);
}

export async function replyTask(id: string, text: string): Promise<{ accepted: boolean }> {
  return api(`/api/tasks/${id}/reply`, { method: "POST", body: JSON.stringify({ text }) });
}

export async function listAuditLogs(params: {
  page?: number;
  page_size?: number;
  action?: string;
  actor?: string;
  start?: string;
  end?: string;
}): Promise<Paged<AuditLogItem>> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.action) query.set("action", params.action);
  if (params.actor) query.set("actor", params.actor);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  return api<Paged<AuditLogItem>>(`/api/audit-logs?${query.toString()}`);
}

export async function listAppLogs(params: {
  page?: number;
  page_size?: number;
  level?: string;
  logger?: string;
  search?: string;
  start?: string;
  end?: string;
}): Promise<Paged<AppLogItem>> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.level) query.set("level", params.level);
  if (params.logger) query.set("logger", params.logger);
  if (params.search) query.set("search", params.search);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  return api<Paged<AppLogItem>>(`/api/app-logs?${query.toString()}`);
}

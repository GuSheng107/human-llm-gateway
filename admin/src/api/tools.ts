import { api } from "./client";

export interface ToolArgumentProperty {
  type: string;
  description: string | null;
}

export interface ToolArgumentsSchema {
  type: string;
  properties: Record<string, ToolArgumentProperty>;
  required?: string[];
}

export interface ToolItem {
  id: string;
  name: string;
  description: string | null;
  command_template: string | null;
  arguments_schema: ToolArgumentsSchema | null;
  timeout_seconds: number;
  is_enabled: boolean;
  created_at: string;
}

export interface ToolPage {
  items: ToolItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface ToolExecutionItem {
  id: string;
  tool_id: string;
  tool_name: string;
  state: string;
  exit_code: number | null;
  stdout: string | null;
  stderr: string | null;
  error_code: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface ToolExecutionPage {
  items: ToolExecutionItem[];
  page: number;
  page_size: number;
  total: number;
}

export function listTools(page = 1, enabledOnly = false): Promise<ToolPage> {
  const query = new URLSearchParams({ page: String(page), page_size: "50" });
  if (enabledOnly) query.set("enabled_only", "true");
  return api<ToolPage>(`/api/tools?${query}`);
}

export function createTool(payload: {
  name: string;
  description?: string | null;
  command_template: string;
  arguments_schema: ToolArgumentsSchema;
  timeout_seconds: number;
}): Promise<ToolItem> {
  return api<ToolItem>("/api/tools", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateTool(
  id: string,
  payload: Record<string, unknown>,
): Promise<ToolItem> {
  return api<ToolItem>(`/api/tools/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteTool(id: string): Promise<void> {
  await api<void>(`/api/tools/${id}`, { method: "DELETE" });
}

export function executeTool(
  id: string,
  arguments_: Record<string, string>,
): Promise<ToolExecutionItem> {
  return api<ToolExecutionItem>(`/api/tools/${id}/execute`, {
    method: "POST",
    body: JSON.stringify({ arguments: arguments_, confirmed: true }),
  });
}

export function listToolExecutions(page = 1): Promise<ToolExecutionPage> {
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  return api<ToolExecutionPage>(`/api/tools/executions?${query}`);
}
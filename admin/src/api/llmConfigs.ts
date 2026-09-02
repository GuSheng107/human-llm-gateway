import { api } from "./client";
import type { LlmConfig, LlmConfigTestResult, Page } from "../types/gateway";

export type LlmProtocol = "openai_chat" | "openai_responses" | "anthropic_messages";
export type ThinkingMode = "model_default" | "enabled" | "disabled";
export type ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface LlmConfigPayload {
  name: string;
  protocol: LlmProtocol;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  enabled?: boolean;
  default_temperature?: number | null;
  default_top_p?: number | null;
  default_top_k?: number | null;
  max_output_tokens?: number | null;
  context_window_input?: number | null;
  context_window_output?: number | null;
  max_tool_call_rounds?: number;
  supports_image_input?: boolean;
  thinking_mode?: ThinkingMode;
  thinking_level?: ThinkingLevel | null;
  extra_body?: Record<string, unknown>;
}

export interface LlmConfigUpdatePayload {
  name?: string;
  protocol?: LlmProtocol;
  base_url?: string;
  api_key?: string;
  model?: string;
  timeout_seconds?: number;
  enabled?: boolean;
  default_temperature?: number | null;
  default_top_p?: number | null;
  default_top_k?: number | null;
  max_output_tokens?: number | null;
  context_window_input?: number | null;
  context_window_output?: number | null;
  max_tool_call_rounds?: number;
  supports_image_input?: boolean;
  thinking_mode?: ThinkingMode;
  thinking_level?: ThinkingLevel | null;
  extra_body?: Record<string, unknown>;
}

export function listLlmConfigs(page: number, search = "", pageSize = 20): Promise<Page<LlmConfig>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<LlmConfig>>(`/api/llm-configs?${query}`);
}

export function createLlmConfig(payload: LlmConfigPayload): Promise<LlmConfig> {
  return api<LlmConfig>("/api/llm-configs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateLlmConfig(
  id: string,
  payload: LlmConfigUpdatePayload,
): Promise<LlmConfig> {
  return api<LlmConfig>(`/api/llm-configs/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteLlmConfig(id: string): Promise<void> {
  await api<void>(`/api/llm-configs/${id}`, { method: "DELETE" });
}

export function testLlmConfig(id: string): Promise<LlmConfigTestResult> {
  return api<LlmConfigTestResult>(`/api/llm-configs/${id}/test`, {
    method: "POST",
  });
}

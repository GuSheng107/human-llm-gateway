import { api } from "./client";
import type { LlmConfig, LlmConfigTestResult, Page } from "../types/gateway";

export type LlmProtocol = "openai_compatible" | "anthropic";

export interface LlmConfigHeaderInput {
  name: string;
  value: string;
}

export interface LlmConfigPayload {
  name: string;
  protocol: LlmProtocol;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  headers?: LlmConfigHeaderInput[];
  enabled?: boolean;
}

export interface LlmConfigUpdatePayload {
  name?: string;
  protocol?: LlmProtocol;
  base_url?: string;
  api_key?: string;
  model?: string;
  timeout_seconds?: number;
  headers?: LlmConfigHeaderInput[];
  enabled?: boolean;
}

export function listLlmConfigs(page: number, search = ""): Promise<Page<LlmConfig>> {
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
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
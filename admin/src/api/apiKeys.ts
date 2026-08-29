import { api } from "./client";
import type { ApiKey, ApiKeyCreated, Page } from "../types/gateway";

export interface ApiKeyPayload {
  name: string;
  enabled?: boolean;
  delivery_mode?: "web" | "im";
  im_connection_id?: number | null;
  reply_strategy?: "human" | "llm" | "human_fallback_llm";
  llm_config_id?: number | null;
  human_timeout_seconds?: number;
  model_group_id?: number | null;
  fake_model_ids?: number[];
}

export function listApiKeys(page: number, search = ""): Promise<Page<ApiKey>> {
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<ApiKey>>(`/api/api-keys?${query}`);
}

export function createApiKey(payload: ApiKeyPayload): Promise<ApiKeyCreated> {
  return api<ApiKeyCreated>("/api/api-keys", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApiKey(id: string, payload: Partial<ApiKeyPayload>): Promise<ApiKey> {
  return api<ApiKey>(`/api/api-keys/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteApiKey(id: string): Promise<void> {
  await api<void>(`/api/api-keys/${id}`, { method: "DELETE" });
}

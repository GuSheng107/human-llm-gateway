import { api } from "./client";
import type { Paged } from "../types/tasks";
import type {
  ApiKey,
  ApiKeyCreated,
  CatalogEntry,
  ModelRoute,
  Provider,
  ProviderDetail,
  RouteMode,
  SyncedModel,
} from "../types/llm";

export async function listProviders(page = 1, pageSize = 50): Promise<Paged<Provider>> {
  return api<Paged<Provider>>(`/api/providers?page=${page}&page_size=${pageSize}`);
}

export async function getProvider(id: number): Promise<ProviderDetail> {
  return api<ProviderDetail>(`/api/providers/${id}`);
}

export async function createProvider(payload: {
  name: string;
  base_url: string;
  protocol: string;
  api_key?: string;
}): Promise<Provider> {
  return api<Provider>("/api/providers", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateProvider(
  id: number,
  payload: Record<string, unknown>,
): Promise<Provider> {
  return api<Provider>(`/api/providers/${id}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteProvider(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/providers/${id}/delete`, { method: "POST" });
}

export async function validateProvider(id: number): Promise<{ valid: boolean; error?: string; model_count?: number }> {
  return api(`/api/providers/${id}/validate`, { method: "POST" });
}

export async function syncProviderModels(id: number): Promise<{ data: SyncedModel[] }> {
  return api(`/api/providers/${id}/models/sync`, { method: "POST" });
}

export async function listProviderModels(id: number): Promise<{ data: SyncedModel[] }> {
  return api(`/api/providers/${id}/models`);
}

export async function listRoutes(page = 1, pageSize = 50): Promise<Paged<ModelRoute>> {
  return api<Paged<ModelRoute>>(`/api/model-routes?page=${page}&page_size=${pageSize}`);
}

export async function createRoute(payload: {
  name: string;
  model_name: string;
  upstream_model?: string;
  mode: RouteMode;
  provider_id?: number | null;
  human_timeout_seconds?: number;
}): Promise<ModelRoute> {
  return api<ModelRoute>("/api/model-routes", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateRoute(
  id: number,
  payload: Record<string, unknown>,
): Promise<ModelRoute> {
  return api<ModelRoute>(`/api/model-routes/${id}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteRoute(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/model-routes/${id}/delete`, { method: "POST" });
}

export async function listCatalog(): Promise<CatalogEntry[]> {
  return api<CatalogEntry[]>("/api/model-catalog");
}

export async function createCatalogEntry(payload: {
  model_id: string;
  owned_by?: string;
}): Promise<CatalogEntry> {
  return api<CatalogEntry>("/api/model-catalog", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateCatalogEntry(
  id: number,
  payload: Record<string, unknown>,
): Promise<CatalogEntry> {
  return api<CatalogEntry>(`/api/model-catalog/${id}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteCatalogEntry(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/model-catalog/${id}/delete`, { method: "POST" });
}

export async function listApiKeys(page = 1, pageSize = 50): Promise<Paged<ApiKey>> {
  return api<Paged<ApiKey>>(`/api/api-keys?page=${page}&page_size=${pageSize}`);
}

export async function createApiKey(payload: {
  name: string;
  route_id: number;
  im_connection_id?: number | null;
  operator_name?: string;
}): Promise<ApiKeyCreated> {
  return api<ApiKeyCreated>("/api/api-keys", { method: "POST", body: JSON.stringify(payload) });
}

export async function deleteApiKey(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/api-keys/${id}/delete`, { method: "POST" });
}

export async function toggleApiKey(id: number): Promise<{ active: boolean }> {
  return api(`/api/api-keys/${id}/disable`, { method: "POST" });
}

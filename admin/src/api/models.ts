import { api } from "./client";
import type { FakeModel, ModelGroup, Page } from "../types/gateway";

export interface FakeModelPayload {
  model_id: string;
  display_name?: string | null;
  description?: string | null;
  sort_order?: number;
  enabled?: boolean;
}

export interface GroupPayload {
  name: string;
  description?: string | null;
  enabled?: boolean;
}

export function listFakeModels(search = ""): Promise<Page<FakeModel>> {
  const query = new URLSearchParams({ page: "1", page_size: "100" });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<FakeModel>>(`/api/fake-models?${query}`);
}

export function createFakeModel(payload: FakeModelPayload): Promise<FakeModel> {
  return api<FakeModel>("/api/fake-models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateFakeModel(
  id: string,
  payload: { enabled?: boolean; display_name?: string | null; sort_order?: number },
): Promise<FakeModel> {
  return api<FakeModel>(`/api/fake-models/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteFakeModel(id: string): Promise<void> {
  await api<void>(`/api/fake-models/${id}`, { method: "DELETE" });
}

export function listModelGroups(page = 1): Promise<Page<ModelGroup>> {
  return api<Page<ModelGroup>>(`/api/model-groups?page=${page}&page_size=100`);
}

export function createModelGroup(payload: GroupPayload): Promise<ModelGroup> {
  return api<ModelGroup>("/api/model-groups", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateModelGroup(
  id: string,
  payload: { enabled?: boolean; name?: string; description?: string | null },
): Promise<ModelGroup> {
  return api<ModelGroup>(`/api/model-groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function replaceGroupMembers(id: string, fakeModelIds: number[]): Promise<ModelGroup> {
  return api<ModelGroup>(`/api/model-groups/${id}/models`, {
    method: "PUT",
    body: JSON.stringify({ fake_model_ids: fakeModelIds }),
  });
}

export async function deleteModelGroup(id: string): Promise<void> {
  await api<void>(`/api/model-groups/${id}`, { method: "DELETE" });
}

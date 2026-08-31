import { api } from "./client";
import type { FakeModel, ModelGroup, Page } from "../types/gateway";

export type Capability =
  | "vision"
  | "tools"
  | "thinking"
  | "image_gen"
  | "audio"
  | "video"
  | "streaming"
  | "function_calling";

export const CAPABILITY_LABELS: Record<string, string> = {
  vision: "视觉",
  tools: "工具",
  thinking: "思考",
  image_gen: "绘图",
  audio: "音频",
  video: "视频",
  streaming: "流式",
  function_calling: "函数调用",
};

export const ENDPOINT_LABELS: Record<string, string> = {
  openai_chat: "OpenAI Chat",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
};

export const BILLING_LABELS: Record<string, string> = {
  pay_as_you_go: "按量计费",
  subscription: "订阅",
  free: "免费",
  dynamic: "动态计费",
};

export interface ModelPricing {
  input?: number | null;
  output?: number | null;
  cached_input?: number | null;
  cached_write?: number | null;
}

export interface FakeModelPayload {
  model_id: string;
  display_name?: string | null;
  description?: string | null;
  sort_order?: number;
  enabled?: boolean;
  pricing?: ModelPricing;
  context_window?: number | null;
  max_output_tokens?: number | null;
  capabilities?: string[];
  billing_tier?: string;
  endpoint_type?: string;
  logo_url?: string | null;
  tags?: string[];
}

export type FakeModelUpdatePayload = Partial<Omit<FakeModelPayload, "model_id">> & {
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  cached_input_price_per_million?: number | null;
  cached_write_price_per_million?: number | null;
};

export interface ModelListFilters {
  search?: string;
  provider?: string;
  billing_tier?: string;
  endpoint_type?: string;
  capability?: string;
  tag?: string;
  group_id?: string;
  include_disabled?: boolean;
}

export interface GroupPayload {
  name: string;
  description?: string | null;
  enabled?: boolean;
}

export function listFakeModels(
  filters: ModelListFilters = {},
  page = 1,
  pageSize = 100,
): Promise<Page<FakeModel>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.provider) query.set("provider", filters.provider);
  if (filters.billing_tier) query.set("billing_tier", filters.billing_tier);
  if (filters.endpoint_type) query.set("endpoint_type", filters.endpoint_type);
  if (filters.capability) query.set("capability", filters.capability);
  if (filters.tag) query.set("tag", filters.tag);
  if (filters.group_id) query.set("group_id", filters.group_id);
  if (filters.include_disabled) query.set("include_disabled", "true");
  return api<Page<FakeModel>>(`/api/fake-models?${query}`);
}

const LIST_ALL_PAGE_SIZE = 100;
const LIST_ALL_CONCURRENCY = 4;

export async function listAllFakeModels(filters: ModelListFilters = {}): Promise<FakeModel[]> {
  const first = await listFakeModels(filters, 1, LIST_ALL_PAGE_SIZE);
  const items = [...first.items];
  const totalPages = Math.ceil(first.total / LIST_ALL_PAGE_SIZE);
  // 首页已知 total 后，剩余页按固定并发分批拉取；任一请求失败即整体抛错，由调用方提示重试。
  for (let start = 2; start <= totalPages; start += LIST_ALL_CONCURRENCY) {
    const batchPages = Array.from(
      { length: Math.min(LIST_ALL_CONCURRENCY, totalPages - start + 1) },
      (_, index) => start + index,
    );
    const batch = await Promise.all(
      batchPages.map((page) => listFakeModels(filters, page, LIST_ALL_PAGE_SIZE)),
    );
    for (const result of batch) items.push(...result.items);
  }
  return items.slice(0, first.total);
}

export function createFakeModel(payload: FakeModelPayload): Promise<FakeModel> {
  return api<FakeModel>("/api/fake-models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateFakeModel(
  id: string,
  payload: FakeModelUpdatePayload,
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

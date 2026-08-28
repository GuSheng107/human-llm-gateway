export type RouteMode = "human" | "llm" | "human_fallback_llm";

export interface Provider {
  id: number;
  name: string;
  protocol: string;
  base_url: string;
  active: boolean;
  owner_id?: number;
  owner_name?: string;
}

export interface ProviderDetail extends Provider {
  has_api_key: boolean;
  options: Record<string, unknown>;
}

export interface SyncedModel {
  id: string;
  object: string;
  owned_by: string;
}

export interface ModelRoute {
  id: number;
  name: string;
  model_name: string;
  upstream_model: string;
  model_names: string[];
  mode: RouteMode;
  provider_id: number | null;
  provider_name?: string;
  human_timeout_seconds: number;
  owner_id?: number;
}

export interface CatalogEntry {
  id: number;
  model_id: string;
  owned_by: string;
  sort_order: number;
  active: boolean;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  active: boolean;
  operator_name: string;
  im_name: string;
  im_connection_id: number | null;
  binding_type: "im" | "web";
  platform: string | null;
  route_id: number;
  route_mode: RouteMode;
  model_name: string;
  owner_id: number;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  secret: string;
}

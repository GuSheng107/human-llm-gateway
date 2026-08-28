import { api } from "./client";
import type {
  BindingCode,
  BindingStatus,
  ConnectionHealth,
  ImConnection,
  Page,
  PlatformSpec,
} from "../types/gateway";

export interface ConnectionPayload {
  name: string;
  platform: string;
  config: Record<string, string>;
}

export function listPlatforms(): Promise<PlatformSpec[]> {
  return api<PlatformSpec[]>("/api/im-platforms");
}

export function listConnections(page: number, search = ""): Promise<Page<ImConnection>> {
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<ImConnection>>(`/api/im-connections?${query}`);
}

export function createConnection(payload: ConnectionPayload): Promise<ImConnection> {
  return api<ImConnection>("/api/im-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateConnection(
  id: string,
  payload: { name?: string; config?: Record<string, string> },
): Promise<ImConnection> {
  return api<ImConnection>(`/api/im-connections/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteConnection(id: string): Promise<void> {
  await api<void>(`/api/im-connections/${id}`, { method: "DELETE" });
}

export function startConnection(id: string): Promise<ImConnection> {
  return api<ImConnection>(`/api/im-connections/${id}/start`, { method: "POST" });
}

export function stopConnection(id: string): Promise<ImConnection> {
  return api<ImConnection>(`/api/im-connections/${id}/stop`, { method: "POST" });
}

export function applyConnection(id: string): Promise<ImConnection> {
  return api<ImConnection>(`/api/im-connections/${id}/apply`, { method: "POST" });
}

export function connectionHealth(id: string): Promise<ConnectionHealth> {
  return api<ConnectionHealth>(`/api/im-connections/${id}/health`);
}

export function createBinding(id: string): Promise<BindingCode> {
  return api<BindingCode>(`/api/im-connections/${id}/binding`, { method: "POST" });
}

export function bindingStatus(id: string): Promise<BindingStatus> {
  return api<BindingStatus>(`/api/im-connections/${id}/binding/status`);
}

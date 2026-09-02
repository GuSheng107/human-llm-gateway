import { api } from "./client";
import type {
  BindingCode,
  BindingStatus,
  ConnectionCheckItem,
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

export interface ConnectionFilter {
  platform?: string;
  state?: string;
}

export function listConnections(
  page = 1,
  search = "",
  filters: ConnectionFilter = {},
  pageSize = 100,
): Promise<Page<ImConnection>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (search) query.set("search", search);
  if (filters.platform) query.set("platform", filters.platform);
  if (filters.state) query.set("state", filters.state);
  return api<Page<ImConnection>>(`/api/im-connections?${query}`);
}

export function adminListConnections(
  page = 1,
  search = "",
  filters: ConnectionFilter = {},
  pageSize = 20,
): Promise<Page<ImConnection>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (search) query.set("search", search);
  if (filters.platform) query.set("platform", filters.platform);
  if (filters.state) query.set("state", filters.state);
  return api<Page<ImConnection>>(`/api/admin/im-connections?${query}`);
}

export async function listAllConnections(): Promise<ImConnection[]> {
  const items: ImConnection[] = [];
  let page = 1;
  for (;;) {
    const result = await listConnections(page);
    items.push(...result.items);
    if (items.length >= result.total || result.items.length === 0) return items;
    page += 1;
  }
}

export async function adminListAllConnections(): Promise<ImConnection[]> {
  const items: ImConnection[] = [];
  let page = 1;
  for (;;) {
    const result = await adminListConnections(page, "", {}, 100);
    items.push(...result.items);
    if (items.length >= result.total || result.items.length === 0) return items;
    page += 1;
  }
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

export function checkConnections(): Promise<ConnectionCheckItem[]> {
  return api<ConnectionCheckItem[]>("/api/im-connections/check", { method: "POST" });
}

export function createBinding(id: string): Promise<BindingCode> {
  return api<BindingCode>(`/api/im-connections/${id}/binding`, { method: "POST" });
}

export function bindingStatus(id: string): Promise<BindingStatus> {
  return api<BindingStatus>(`/api/im-connections/${id}/binding/status`);
}

export function cancelBinding(id: string): Promise<ImConnection> {
  return api<ImConnection>(`/api/im-connections/${id}/binding/cancel`, { method: "POST" });
}

export interface CredentialRotated {
  field: string;
  token: string;
}

/** 重新生成网关自签 Token：明文只在本次响应展示一次。 */
export function rotateCredential(id: string, field: string): Promise<CredentialRotated> {
  return api<CredentialRotated>(`/api/im-connections/${id}/credentials/${field}/rotate`, {
    method: "POST",
  });
}

export interface QrLoginStart {
  qrcode: string;
  qrcode_img_content: string;
}

export interface QrLoginPoll {
  status: string;
  bound?: boolean;
}

export function startQrLogin(id: string): Promise<QrLoginStart> {
  return api<QrLoginStart>(`/api/im-connections/${id}/login`, { method: "POST" });
}

export function pollQrLogin(id: string): Promise<QrLoginPoll> {
  return api<QrLoginPoll>(`/api/im-connections/${id}/login`);
}

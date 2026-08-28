import { api } from "./client";
import type {
  BindingSnapshot,
  BindingStatusSnapshot,
  ConnectionCreated,
  HealthSnapshot,
  IMConnection,
  PlatformDefinition,
} from "../types/connections";

export async function listPlatforms(): Promise<PlatformDefinition[]> {
  return api<PlatformDefinition[]>("/api/im-platforms");
}

export async function listConnections(): Promise<IMConnection[]> {
  return api<IMConnection[]>("/api/im-connections");
}

export async function getConnection(id: number): Promise<IMConnection> {
  return api<IMConnection>(`/api/im-connections/${id}`);
}

export async function createConnection(payload: {
  name: string;
  platform: string;
  config: Record<string, unknown>;
}): Promise<ConnectionCreated> {
  return api<ConnectionCreated>("/api/im-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateConnection(
  id: number,
  payload: { name?: string; config?: Record<string, unknown> },
): Promise<IMConnection> {
  return api<IMConnection>(`/api/im-connections/${id}/update`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteConnection(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/im-connections/${id}/delete`, { method: "POST" });
}

export async function startConnection(id: number): Promise<HealthSnapshot> {
  return api(`/api/im-connections/${id}/start`, { method: "POST" });
}

export async function stopConnection(id: number): Promise<{ stopped: boolean }> {
  return api(`/api/im-connections/${id}/stop`, { method: "POST" });
}

export async function applyConnection(id: number): Promise<IMConnection> {
  return api(`/api/im-connections/${id}/apply`, { method: "POST" });
}

export async function fetchHealth(id: number): Promise<HealthSnapshot> {
  return api(`/api/im-connections/${id}/health`);
}

export async function startLogin(id: number): Promise<HealthSnapshot> {
  return api(`/api/im-connections/${id}/login`, { method: "POST" });
}

export async function pollLogin(id: number): Promise<HealthSnapshot> {
  return api(`/api/im-connections/${id}/login`);
}

export async function startBinding(id: number): Promise<BindingSnapshot> {
  return api(`/api/im-connections/${id}/binding`, { method: "POST" });
}

export async function fetchBindingStatus(id: number): Promise<BindingStatusSnapshot> {
  return api(`/api/im-connections/${id}/binding/status`);
}

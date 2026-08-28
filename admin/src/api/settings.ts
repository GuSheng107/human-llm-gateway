import { api } from "./client";

export async function getSettings(): Promise<{ items: Record<string, unknown>; keys: string[] }> {
  return api("/api/settings");
}

export async function updateSettings(payload: Record<string, unknown>): Promise<{
  updated: Record<string, unknown>;
  items: Record<string, unknown>;
}> {
  return api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
}

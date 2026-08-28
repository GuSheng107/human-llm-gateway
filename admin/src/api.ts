import type { LoginResponse } from "./types";

export const TOKEN_KEY = "hlg_admin_token";

function detailMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const prefix = import.meta.env.DEV ? "/api" : "";
  const response = await fetch(prefix + path, { ...init, headers });
  if (response.status === 401) localStorage.removeItem(TOKEN_KEY);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(detailMessage(body, `请求失败（${response.status}）`));
  return body as T;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const result = await api<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(TOKEN_KEY, result.access_token);
  return result;
}

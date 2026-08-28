import type { LoginResponse } from "../types/auth";

export interface ApiErrorBody {
  code: string;
  message: string;
  action: string;
  details: Record<string, unknown>;
  request_id: string;
}

export class ApiError extends Error {
  code: string;
  action: string;
  details: Record<string, unknown>;
  requestId: string;
  status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.action = body.action;
    this.details = body.details;
    this.requestId = body.request_id;
  }
}

export const TOKEN_KEY = "hlg_admin_token";

function parseErrorBody(body: unknown, fallbackStatus: number): ApiErrorBody {
  if (body && typeof body === "object" && "error" in body) {
    const raw = (body as { error: unknown }).error;
    if (raw && typeof raw === "object") {
      const e = raw as Partial<ApiErrorBody>;
      return {
        code: e.code ?? "unknown",
        message: e.message ?? "请求失败",
        action: e.action ?? "none",
        details: e.details ?? {},
        request_id: e.request_id ?? "",
      };
    }
  }
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return { code: "unknown", message: detail, action: "none", details: {}, request_id: "" };
    }
  }
  return {
    code: "unknown",
    message: `请求失败（${fallbackStatus}）`,
    action: "none",
    details: {},
    request_id: "",
  };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event("hlg:unauthorized"));
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, parseErrorBody(body, response.status));
  return body as T;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const result = await api<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(TOKEN_KEY, result.access_token);
  return result;
}

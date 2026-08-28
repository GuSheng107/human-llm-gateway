import { api } from "./client";
import type { CurrentUser } from "../types/auth";

export async function fetchMe(): Promise<CurrentUser> {
  return api<CurrentUser>("/api/auth/me");
}

export async function revokeCurrentSession(): Promise<void> {
  await api<void>("/api/auth/logout", { method: "POST" });
}

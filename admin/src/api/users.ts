import { api } from "./client";
import type { Page, UserDetail, UserSummary } from "../types/governance";

export interface UserCreated extends UserSummary {
  temporary_password: string | null;
}

export interface PasswordResetResult {
  user: UserSummary;
  temporary_password: string | null;
}

export function listUsers(page: number, search = "", pageSize = 20): Promise<Page<UserSummary>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<UserSummary>>(`/api/users?${query}`);
}

export function getUser(id: string): Promise<UserDetail> {
  return api<UserDetail>(`/api/users/${id}`);
}

export function createUser(payload: {
  username: string;
  display_name: string;
  password?: string;
}): Promise<UserCreated> {
  return api<UserCreated>("/api/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateUser(
  id: string,
  payload: { display_name?: string; is_active?: boolean },
): Promise<UserDetail> {
  return api<UserDetail>(`/api/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function resetUserPassword(
  id: string,
  password?: string,
): Promise<PasswordResetResult> {
  return api<PasswordResetResult>(`/api/users/${id}/reset-password`, {
    method: "POST",
    body: JSON.stringify(password ? { password } : {}),
  });
}

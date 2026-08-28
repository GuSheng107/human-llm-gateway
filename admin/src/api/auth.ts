import { api } from "./client";
import type { CurrentUser } from "../types/auth";

export async function fetchMe(): Promise<CurrentUser> {
  return api<CurrentUser>("/api/auth/me");
}

export async function listUsers(): Promise<CurrentUser[]> {
  return api<CurrentUser[]>("/api/users");
}

export async function createUser(payload: {
  username: string;
  display_name: string;
  password: string;
}): Promise<CurrentUser> {
  return api<CurrentUser>("/api/users", { method: "POST", body: JSON.stringify(payload) });
}

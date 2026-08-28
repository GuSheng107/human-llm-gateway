import { api } from "./client";
import type { CurrentUser } from "../types/auth";

export interface CaptchaResult {
  captcha_token: string;
  captcha_image: string;
}

export async function fetchCaptcha(): Promise<CaptchaResult> {
  return api<CaptchaResult>("/api/auth/captcha");
}

export async function fetchMe(): Promise<CurrentUser> {
  return api<CurrentUser>("/api/auth/me");
}

export async function revokeCurrentSession(): Promise<void> {
  await api<void>("/api/auth/logout", { method: "POST" });
}

export async function registerAccount(payload: {
  invitation_code: string;
  username: string;
  display_name: string;
  password: string;
  email?: string | null;
}): Promise<CurrentUser> {
  return api<CurrentUser>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateProfile(payload: {
  display_name: string;
  email?: string | null;
  avatar_base64?: string | null;
}): Promise<CurrentUser> {
  return api<CurrentUser>("/api/account/profile", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function changePassword(
  current_password: string,
  new_password: string,
): Promise<CurrentUser> {
  return api<CurrentUser>("/api/account/password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

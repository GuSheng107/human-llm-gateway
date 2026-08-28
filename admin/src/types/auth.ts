export type UserRole = "admin" | "user";

export interface CurrentUser {
  id: number;
  username: string;
  display_name: string;
  role: UserRole;
}

export interface LoginResponse extends CurrentUser {
  access_token: string;
  token_type: string;
}

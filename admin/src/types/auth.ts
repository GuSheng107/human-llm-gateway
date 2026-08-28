export type Capability =
  | "account.password.change"
  | "account.profile.update"
  | "invitation.manage"
  | "user.manage"
  | "connection.manage"
  | "model.manage"
  | "api_key.manage";

export interface CurrentUser {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  must_change_password: boolean;
  capabilities: Capability[];
}

export interface LoginResponse extends CurrentUser {
  access_token: string;
  token_type: string;
}

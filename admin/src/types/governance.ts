export interface Invitation {
  id: string;
  code_prefix: string;
  note: string | null;
  max_uses: number;
  used_count: number;
  status: "active" | "expired" | "exhausted" | "revoked";
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  created_by_user_id: string;
}

export interface InvitationCreated extends Invitation {
  code: string;
}

export interface UserSummary {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
  must_change_password: boolean;
  active_task_count: number;
  registered_via_invitation_id: string | null;
  last_login_at: string | null;
  disabled_at: string | null;
  created_at: string;
}

export interface UserDetail extends UserSummary {
  impact: {
    active_sessions: number;
    enabled_api_keys: number;
    active_tasks: number;
  };
  resource_counts: Record<string, number>;
}

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

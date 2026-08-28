import { api } from "./client";
import type { Invitation, InvitationCreated, Page } from "../types/governance";

export interface InvitationPayload {
  note?: string | null;
  expires_at?: string | null;
  max_uses?: number;
}

export function listInvitations(page: number, search = ""): Promise<Page<Invitation>> {
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  if (search.trim()) query.set("search", search.trim());
  return api<Page<Invitation>>(`/api/invitations?${query}`);
}

export function createInvitation(payload: InvitationPayload): Promise<InvitationCreated> {
  return api<InvitationCreated>("/api/invitations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateInvitation(
  id: string,
  payload: InvitationPayload,
): Promise<Invitation> {
  return api<Invitation>(`/api/invitations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function revokeInvitation(id: string): Promise<Invitation> {
  return api<Invitation>(`/api/invitations/${id}/revoke`, { method: "POST" });
}

export async function deleteInvitation(id: string): Promise<void> {
  await api<void>(`/api/invitations/${id}`, { method: "DELETE" });
}

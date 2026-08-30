import { api } from "./client";
import type {
  AssistantMessage,
  AssistantPageContext,
  AssistantSession,
  AssistantSessionDetail,
} from "../types/gateway";

export interface SendMessagePayload {
  text: string;
  page_context?: AssistantPageContext | null;
}

export function listAssistantSessions(): Promise<AssistantSession[]> {
  return api<AssistantSession[]>("/api/assistant/sessions");
}

export function createAssistantSession(
  title: string,
  llmConfigId: number | null,
): Promise<AssistantSession> {
  return api<AssistantSession>("/api/assistant/sessions", {
    method: "POST",
    body: JSON.stringify({ title, llm_config_id: llmConfigId }),
  });
}

export function getAssistantSession(id: string): Promise<AssistantSessionDetail> {
  return api<AssistantSessionDetail>(`/api/assistant/sessions/${id}`);
}

export async function deleteAssistantSession(id: string): Promise<void> {
  await api<void>(`/api/assistant/sessions/${id}`, { method: "DELETE" });
}

export function sendAssistantMessage(
  sessionId: string,
  payload: SendMessagePayload,
): Promise<AssistantMessage> {
  return api<AssistantMessage>(`/api/assistant/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
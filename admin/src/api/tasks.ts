import { api } from "./client";
import type {
  Page,
  ReplyDraft,
  ReplyResult,
  TaskDetail,
  TaskDraft,
  TaskEvent,
  TaskItem,
  TaskState,
} from "../types/gateway";

export interface TaskListParams {
  page: number;
  search?: string;
  state?: TaskState;
  bucket?: "in_progress" | "finished" | "failed";
  pageSize?: number;
}

export function listTasks(params: TaskListParams): Promise<Page<TaskItem>> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize ?? 20),
  });
  if (params.search && params.search.trim()) query.set("search", params.search.trim());
  if (params.state) query.set("state", params.state);
  if (params.bucket) query.set("bucket", params.bucket);
  return api<Page<TaskItem>>(`/api/tasks?${query}`);
}

export function getTask(id: string): Promise<TaskDetail> {
  return api<TaskDetail>(`/api/tasks/${id}`);
}

export function getTaskRawRequest(id: string): Promise<{
  task_id: string;
  raw_request: Record<string, unknown> | null;
}> {
  return api(`/api/tasks/${id}/raw-request`);
}

export function listTaskEvents(id: string, page: number): Promise<Page<TaskEvent>> {
  const query = new URLSearchParams({ page: String(page), page_size: "50" });
  return api<Page<TaskEvent>>(`/api/tasks/${id}/events?${query}`);
}

export function saveDraft(taskId: string, draft: ReplyDraft): Promise<TaskDraft> {
  return api<TaskDraft>(`/api/tasks/${taskId}/drafts`, {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export function generateDraft(
  taskId: string,
  llmConfigId: number,
): Promise<TaskDraft> {
  return api<TaskDraft>(`/api/tasks/${taskId}/drafts/generate`, {
    method: "POST",
    body: JSON.stringify({ llm_config_id: llmConfigId }),
  });
}

export function updateDraft(
  taskId: string,
  draftId: string,
  draft: ReplyDraft,
  expectedVersion: number,
): Promise<TaskDraft> {
  return api<TaskDraft>(`/api/tasks/${taskId}/drafts/${draftId}`, {
    method: "PATCH",
    body: JSON.stringify({ ...draft, expected_version: expectedVersion }),
  });
}

// ---------------------------------------------------------------------------
// M14 工作台收件箱
// ---------------------------------------------------------------------------

export interface InboxItem {
  id: string;
  public_id: string;
  requested_model: string;
  fake_model_name: string;
  protocol: string;
  state: TaskState;
  human_deadline_at: string | null;
  created_at: string;
  prompt_preview: string;
  has_tools: boolean;
  unread: boolean;
  seen_at: string | null;
  last_seen_event_id: string | null;
  owner_user_id: string | null;
  owner_username: string | null;
}

export interface InboxPage {
  items: InboxItem[];
  waiting_count: number;
  unread_count: number;
}

export function listInbox(): Promise<InboxPage> {
  return api<InboxPage>("/api/tasks/inbox");
}

export interface InboxSummary {
  unread_count: number;
  waiting_count: number;
}

export function getInboxSummary(): Promise<InboxSummary> {
  return api<InboxSummary>("/api/tasks/inbox-summary");
}

export async function markTaskSeen(
  taskId: string,
  lastSeenEventId?: string | null,
): Promise<void> {
  await api<void>(`/api/tasks/${taskId}/seen`, {
    method: "POST",
    body: JSON.stringify(
      lastSeenEventId ? { last_seen_event_id: Number(lastSeenEventId) } : {},
    ),
  });
}

export interface ConversationBlock {
  type: string;
  text?: string | null;
  name?: string | null;
  media_type?: string | null;
  tool_call_id?: string | null;
}

export interface ConversationMessage {
  index: number;
  role: string;
  blocks: ConversationBlock[];
  preview: string;
  length: number;
  has_more: boolean;
}

export interface ConversationPage {
  task_id: string;
  messages: ConversationMessage[];
  total: number;
}

export function getConversation(taskId: string): Promise<ConversationPage> {
  return api<ConversationPage>(`/api/tasks/${taskId}/conversation`);
}

export function getConversationMessage(
  taskId: string,
  index: number,
): Promise<{
  task_id: string;
  index: number;
  role: string;
  blocks: ConversationBlock[];
  full_text: string;
  length: number;
}> {
  return api(`/api/tasks/${taskId}/conversation/messages/${index}`);
}

export async function deleteDraft(taskId: string, draftId: string): Promise<void> {
  await api<void>(`/api/tasks/${taskId}/drafts/${draftId}`, { method: "DELETE" });
}

export function submitReply(
  taskId: string,
  draft: ReplyDraft,
  sourceDraftId?: string,
): Promise<ReplyResult> {
  const body = {
    ...draft,
    source_draft_id: sourceDraftId ? Number(sourceDraftId) : null,
  };
  return api<ReplyResult>(`/api/tasks/${taskId}/reply`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

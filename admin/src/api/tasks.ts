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
}

export function listTasks(params: TaskListParams): Promise<Page<TaskItem>> {
  const query = new URLSearchParams({ page: String(params.page), page_size: "20" });
  if (params.search && params.search.trim()) query.set("search", params.search.trim());
  if (params.state) query.set("state", params.state);
  return api<Page<TaskItem>>(`/api/tasks?${query}`);
}

export function getTask(id: string): Promise<TaskDetail> {
  return api<TaskDetail>(`/api/tasks/${id}`);
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

export function updateDraft(
  taskId: string,
  draftId: string,
  draft: ReplyDraft,
): Promise<TaskDraft> {
  return api<TaskDraft>(`/api/tasks/${taskId}/drafts/${draftId}`, {
    method: "PATCH",
    body: JSON.stringify(draft),
  });
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

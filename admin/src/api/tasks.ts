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

export type DraftGenerateMode = "reasoning" | "reply" | "both";

export interface DraftGeneratePayload {
  llm_config_id: number;
  mode?: DraftGenerateMode;
  /** 一次生成操作的短生命周期引导；不落库、不进入最终回复。 */
  generation_instruction?: string | null;
  /** 是否携带调用方 system（RequestView 的 caller_system）。 */
  include_caller_system?: boolean;
  /** 被排除的附带上下文的稳定 ctx ID（current_input 不可排除）。 */
  excluded_context_item_ids?: string[];
  /** 是否携带附件；附件无法承载时后端返回 422 而不是静默降级。 */
  include_attachments?: boolean;
  /** mode=reply 时可携带人工已确认的思考链作为生成依据。 */
  reasoning_seed?: string | null;
}

export function generateDraft(
  taskId: string,
  payload: DraftGeneratePayload,
): Promise<TaskDraft> {
  return api<TaskDraft>(`/api/tasks/${taskId}/drafts/generate`, {
    method: "POST",
    body: JSON.stringify(payload),
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
  api_key_name: string;
  display_name: string;
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

// ---------------------------------------------------------------------------
// 请求视图（RequestView）：工作台与生成弹窗共用的上下文投影
// ---------------------------------------------------------------------------

export interface RequestViewBlock {
  id: string;
  type:
    | "text"
    | "image"
    | "audio"
    | "file"
    | "tool_call"
    | "tool_result"
    | "technical"
    | "unknown";
  /** text/tool_call/tool_result 块的截断预览或完整内容。 */
  text?: string | null;
  text_length?: number | null;
  truncated?: boolean | null;
  /** file/audio 块的文件名。 */
  name?: string | null;
  media_type?: string | null;
  filename?: string | null;
  source?: string | null;
  url?: string | null;
  size_bytes?: number | null;
  previewable?: boolean | null;
  call_id?: string | null;
  arguments?: Record<string, unknown> | null;
  raw_type?: string | null;
}

export interface RequestViewContextItem {
  /** 稳定 ctx ID（ctx_ 前缀）；生成弹窗的排除勾选以此为准。 */
  id: string;
  role: string;
  blocks: RequestViewBlock[];
  text_length: number;
  block_count: number;
}

export interface CallerSystemView {
  items: RequestViewContextItem[];
  item_count: number;
  character_count: number;
  collapsed_by_default: boolean;
}

export interface ToolDefinitionView {
  name: string;
  description: string | null;
  input_schema: Record<string, unknown> | null;
  source_type: string;
  is_generatable: boolean;
}

export interface CallerToolsView {
  definitions: ToolDefinitionView[];
  choice: "auto" | "none" | "required" | "tool" | string;
  required_name: string | null;
  parallel_allowed: boolean;
}

export interface ToolCallWarningView {
  required: boolean;
  acknowledged: boolean;
  acknowledged_at: string | null;
}

export interface RequestView {
  task: {
    id: string;
    public_id: string;
    request_id: string;
    protocol: string;
    requested_model: string;
    state: string;
    created_at: string | null;
    deadline_at: string | null;
  };
  current_input: RequestViewContextItem[];
  caller_system: CallerSystemView;
  attached_context: RequestViewContextItem[];
  attachments: RequestViewBlock[];
  caller_tools: CallerToolsView;
  tool_call_warning: ToolCallWarningView;
  raw_request_available: boolean;
}

export function getRequestView(taskId: string): Promise<RequestView> {
  return api<RequestView>(`/api/tasks/${taskId}/request-view`);
}

/** 拉取被截断块的完整内容（base64 仅在此端点出现）。 */
export function getRequestViewBlock(
  taskId: string,
  blockId: string,
): Promise<Partial<RequestViewBlock>> {
  return api(`/api/tasks/${taskId}/request-view/blocks/${blockId}`);
}

/** 首次使用调用方工具的风险告知确认（服务端状态，替代 sessionStorage）。 */
export function acknowledgeToolCallWarning(
  taskId: string,
): Promise<{ task_id: string; acknowledged: boolean; acknowledged_at: string }> {
  return api(`/api/tasks/${taskId}/tool-call-warning/acknowledge`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export interface ToolArgumentsGeneratePayload {
  llm_config_id: number;
  generation_instruction?: string | null;
  include_caller_system?: boolean;
  excluded_context_item_ids?: string[];
  include_attachments?: boolean;
  current_arguments?: Record<string, unknown> | null;
}

export interface ToolArgumentsGenerateView {
  tool_name: string;
  arguments: Record<string, unknown>;
  llm_config_id: string;
  schema_valid: boolean;
  warnings: string[];
}

/** 小助手按 JSON Schema 与请求上下文生成调用方工具参数。 */
export function generateToolArguments(
  taskId: string,
  toolName: string,
  payload: ToolArgumentsGeneratePayload,
): Promise<ToolArgumentsGenerateView> {
  return api(`/api/tasks/${taskId}/tools/${encodeURIComponent(toolName)}/arguments/generate`, {
    method: "POST",
    body: JSON.stringify(payload),
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

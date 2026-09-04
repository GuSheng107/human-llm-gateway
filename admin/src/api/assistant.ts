import { api, ApiError, TOKEN_KEY, type ApiErrorBody } from "./client";
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

export function patchAssistantSession(
  id: string,
  patch: { title?: string },
): Promise<AssistantSession> {
  return api<AssistantSession>(`/api/assistant/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
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

// ---------------------------------------------------------------------------
// SSE 流式发送（POST + fetch ReadableStream；事件：delta / done / error）
// ---------------------------------------------------------------------------

export interface AssistantStreamCallbacks {
  onDelta?: (text: string) => void;
  onCompress?: (text: string) => void;
  onDone?: (message: AssistantMessage, traceId: string | null) => void;
  onError?: (code: string, message: string, traceId: string | null) => void;
}

interface AssistantStreamEvent {
  type: "delta" | "done" | "error" | "compress";
  text?: string;
  message?: AssistantMessage;
  code?: string;
  message_text?: string;
  trace_id?: string | null;
}

/** 消费一个 SSE data 帧（提取 data: 行并解析 JSON）。 */
function parseSseFrame(frame: string): AssistantStreamEvent | null {
  const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
  if (!dataLine) return null;
  const jsonText = dataLine.slice(5).trim();
  if (!jsonText) return null;
  try {
    const parsed = JSON.parse(jsonText) as Record<string, unknown>;
    const type = typeof parsed.type === "string" ? parsed.type : "";
    const traceId =
      typeof parsed.trace_id === "string" && parsed.trace_id ? parsed.trace_id : null;
    if (type === "error") {
      return {
        type: "error",
        code: typeof parsed.code === "string" ? parsed.code : "unknown",
        message_text: typeof parsed.message === "string" ? parsed.message : "回复失败",
        trace_id: traceId,
      };
    }
    if (type === "delta" && typeof parsed.text === "string") {
      return { type: "delta", text: parsed.text, trace_id: traceId };
    }
    if (type === "compress") {
      return {
        type: "compress",
        text: typeof parsed.text === "string" ? parsed.text : "历史已压缩",
        trace_id: traceId,
      };
    }
    if (type === "done" && parsed.message && typeof parsed.message === "object") {
      return {
        type: "done",
        message: parsed.message as AssistantMessage,
        trace_id: traceId,
      };
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 流式发送消息。前置 HTTP 错误（401/404/400 等）以 ApiError 抛出；
 * 流中错误通过 onError 回调交付（HTTP 已 200）。
 */
export async function streamAssistantMessage(
  sessionId: string,
  payload: SendMessagePayload,
  callbacks: AssistantStreamCallbacks,
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);
  const response = await fetch(`/api/assistant/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (response.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event("hlg:unauthorized"));
  }
  if (!response.ok || !response.body) {
    const errorBody: unknown = await response.json().catch(() => null);
    // 与 api() 的错误结构保持一致（{error:{code,message}} 或 FastAPI detail）。
    let code = "unknown";
    let message = `请求失败（${response.status}）`;
    if (errorBody && typeof errorBody === "object") {
      const body = errorBody as { error?: unknown; detail?: unknown };
      const error = body.error;
      if (error && typeof error === "object") {
        const e = error as Partial<ApiErrorBody>;
        code = e.code ?? code;
        message = e.message ?? message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    }
    throw new ApiError(response.status, {
      code,
      message,
      action: "none",
      details: {},
      request_id: "",
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const index = buffer.indexOf("\n\n");
      if (index === -1) break;
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const event = parseSseFrame(frame);
      if (!event) continue;
      if (event.type === "delta" && event.text) {
        callbacks.onDelta?.(event.text);
      } else if (event.type === "compress") {
        callbacks.onCompress?.(event.text ?? "历史已压缩");
      } else if (event.type === "done" && event.message) {
        callbacks.onDone?.(event.message, event.trace_id ?? null);
      } else if (event.type === "error") {
        callbacks.onError?.(
          event.code ?? "unknown",
          event.message_text ?? "回复失败",
          event.trace_id ?? null,
        );
      }
    }
  }
}
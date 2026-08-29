import type { ReplyDraft, ToolCall } from "../../types/gateway";

const FENCE = ":::";

export function isEmptyDraft(draft: ReplyDraft): boolean {
  return !(
    draft.reasoning ||
    draft.tool_calls.length > 0 ||
    (draft.final_text && draft.final_text.trim())
  );
}

export function serializeReply(draft: ReplyDraft): string {
  const parts: string[] = [];
  if (draft.reasoning) {
    parts.push(`${FENCE} reasoning\n${draft.reasoning}\n${FENCE}`);
  }
  for (const call of draft.tool_calls) {
    const argumentsJson = JSON.stringify(call.arguments);
    parts.push(`${FENCE} tool ${call.id} ${call.name}\n${argumentsJson}\n${FENCE}`);
  }
  if (draft.final_text) parts.push(draft.final_text);
  return parts.join("\n\n");
}

export function parseReply(body: string): ReplyDraft {
  let reasoning: string | null = null;
  const toolCalls: ToolCall[] = [];
  const freeLines: string[] = [];
  const lines = body.split("\n");
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    const stripped = line.trim();
    if (stripped === FENCE || !stripped.startsWith(FENCE)) {
      if (stripped || freeLines.length > 0) freeLines.push(line);
      i += 1;
      continue;
    }
    const spec = stripped.slice(FENCE.length).trim();
    i += 1;
    const content: string[] = [];
    while (i < n && lines[i].trim() !== FENCE) {
      content.push(lines[i]);
      i += 1;
    }
    if (i < n) i += 1;
    const blockText = content.join("\n").trim();
    if (spec === "reasoning") {
      reasoning = blockText;
      continue;
    }
    const parts = spec.split(/\s+/);
    if (parts[0] !== "tool" || parts.length < 3) {
      throw new Error(`未知的围栏类型: ${spec}`);
    }
    const callId = parts[1];
    const name = parts[2];
    toolCalls.push({ id: callId, name, arguments: parseArguments(blockText, callId) });
  }
  const finalText = freeLines.join("\n").trim() || null;
  return { reasoning, tool_calls: toolCalls, final_text: finalText };
}

function parseArguments(content: string, callId: string): Record<string, unknown> {
  if (!content) return {};
  try {
    const value = JSON.parse(content);
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error("not object");
    }
    return value as Record<string, unknown>;
  } catch {
    throw new Error(`tool ${callId} 的 arguments 必须是合法 JSON 对象`);
  }
}

import type { ReplyDraft } from "../../types/gateway";

export function isEmptyDraft(draft: ReplyDraft): boolean {
  return !(
    draft.reasoning ||
    draft.tool_calls.length > 0 ||
    (draft.final_text && draft.final_text.trim())
  );
}

export function serializeReply(draft: ReplyDraft): string {
  // 不再使用围栏 DSL（`::: reasoning` / `::: tool`），仅序列化最终文本。
  return draft.final_text?.trim() ?? "";
}

export function parseReply(body: string): ReplyDraft {
  // 不再解析围栏块；整段正文直接作为 final_text（与后端 dsl.py 同构）。
  const finalText = body.trim() || null;
  return { reasoning: null, tool_calls: [], final_text: finalText };
}

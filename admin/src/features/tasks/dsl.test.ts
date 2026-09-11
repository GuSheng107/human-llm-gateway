import { describe, expect, it } from "vitest";
import { isEmptyDraft, parseReply, serializeReply } from "./dsl";
import type { ReplyDraft } from "../../types/gateway";

// 与后端 app/domain/dsl.py 保持同一语义（tests/test_m6_tasks.py 同构）。
describe("serializeReply", () => {
  it("纯 final_text 序列化为原文本（无围栏）", () => {
    expect(serializeReply({ reasoning: null, tool_calls: [], final_text: "你好世界" })).toBe(
      "你好世界",
    );
  });

  it("空草稿序列化为空串", () => {
    expect(serializeReply({ reasoning: null, tool_calls: [], final_text: null })).toBe("");
  });

  it("reasoning 与 tool_calls 不再参与序列化（仅 final_text）", () => {
    const draft: ReplyDraft = {
      reasoning: "先想想",
      tool_calls: [{ id: "call_1", name: "search", arguments: { q: "test" } }],
      final_text: "最终答案",
    };
    expect(serializeReply(draft)).toBe("最终答案");
  });
});

describe("parseReply", () => {
  it("纯文本解析为 final_text（M4 兼容）", () => {
    const draft = parseReply("纯文本回复，无围栏");
    expect(draft.final_text).toBe("纯文本回复，无围栏");
    expect(draft.reasoning).toBeNull();
    expect(draft.tool_calls).toEqual([]);
  });

  it("围栏块被视为普通文本（不再解析）", () => {
    const body = '::: reasoning\n思考\n:::\n\n::: tool call_1 search\n{"q": "天气"}\n:::\n\n结果如下';
    const draft = parseReply(body);
    expect(draft.final_text).toBe(body);
    expect(draft.reasoning).toBeNull();
    expect(draft.tool_calls).toEqual([]);
  });

  it("空正文解析为 null final_text", () => {
    const draft = parseReply("   ");
    expect(draft.final_text).toBeNull();
    expect(draft.tool_calls).toEqual([]);
  });
});

describe("isEmptyDraft", () => {
  it("空草稿与纯空白为空", () => {
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: null })).toBe(true);
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: "   " })).toBe(true);
  });

  it("有内容不为空", () => {
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: "x" })).toBe(false);
  });

  it("仅 reasoning/tool_calls 而无最终文本视为空（与后端同构）", () => {
    expect(
      isEmptyDraft({ reasoning: null, tool_calls: [{ id: "a", name: "b", arguments: {} }], final_text: null }),
    ).toBe(true);
    expect(isEmptyDraft({ reasoning: "r", tool_calls: [], final_text: null })).toBe(true);
  });
});

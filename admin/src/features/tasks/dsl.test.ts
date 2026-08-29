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

  it("reasoning + tool call + final_text 完整结构", () => {
    const draft: ReplyDraft = {
      reasoning: "先想想",
      tool_calls: [{ id: "call_1", name: "search", arguments: { q: "test" } }],
      final_text: "最终答案",
    };
    const text = serializeReply(draft);
    expect(text).toContain("::: reasoning\n先想想\n:::");
    expect(text).toContain("::: tool call_1 search");
    expect(text).toContain('{"q":"test"}');
    expect(text.endsWith("最终答案")).toBe(true);
  });
});

describe("parseReply", () => {
  it("纯文本解析为 final_text（M4 兼容）", () => {
    const draft = parseReply("纯文本回复，无围栏");
    expect(draft.final_text).toBe("纯文本回复，无围栏");
    expect(draft.reasoning).toBeNull();
    expect(draft.tool_calls).toEqual([]);
  });

  it("围栏块：reasoning / tool / 自由文本", () => {
    const body = '::: reasoning\n思考\n:::\n\n::: tool call_1 search\n{"q": "天气"}\n:::\n\n结果如下';
    const draft = parseReply(body);
    expect(draft.reasoning).toBe("思考");
    expect(draft.tool_calls).toHaveLength(1);
    expect(draft.tool_calls[0].id).toBe("call_1");
    expect(draft.tool_calls[0].name).toBe("search");
    expect(draft.tool_calls[0].arguments).toEqual({ q: "天气" });
    expect(draft.final_text).toBe("结果如下");
  });

  it("tool 空参数解析为空对象", () => {
    const draft = parseReply("::: tool call_0 noop\n:::\n\n正文");
    expect(draft.tool_calls[0].arguments).toEqual({});
    expect(draft.final_text).toBe("正文");
  });

  it("未知围栏类型抛错（不静默忽略）", () => {
    expect(() => parseReply("::: unknown\nx\n:::")).toThrow(/围栏类型/);
  });

  it("tool arguments 非法 JSON 抛错", () => {
    expect(() => parseReply('::: tool c1 fn\nnot-json\n:::')).toThrow(/JSON/);
  });

  it("tool arguments 非对象（数组）抛错", () => {
    expect(() => parseReply("::: tool c1 fn\n[1,2]\n:::")).toThrow(/JSON/);
  });
});

describe("往返一致（与后端 test_m6_tasks 同构）", () => {
  it("parse(serialize(draft)) == draft（全字段）", () => {
    const draft: ReplyDraft = {
      reasoning: "分析",
      tool_calls: [
        { id: "c1", name: "lookup", arguments: { key: "k" } },
        { id: "c2", name: "calc", arguments: { x: 1, y: 2 } },
      ],
      final_text: "结论",
    };
    expect(parseReply(serializeReply(draft))).toEqual(draft);
  });

  it("仅 tool_calls 往返不丢字段", () => {
    const draft: ReplyDraft = {
      reasoning: null,
      tool_calls: [{ id: "t1", name: "fn", arguments: { a: [1, 2] } }],
      final_text: null,
    };
    expect(parseReply(serializeReply(draft))).toEqual(draft);
  });

  it("reasoning 与 final_text 往返", () => {
    const draft: ReplyDraft = { reasoning: "只有思考", tool_calls: [], final_text: "只有正文" };
    expect(parseReply(serializeReply(draft))).toEqual(draft);
  });
});

describe("isEmptyDraft", () => {
  it("空草稿与纯空白为空", () => {
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: null })).toBe(true);
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: "   " })).toBe(true);
  });

  it("有内容不为空", () => {
    expect(isEmptyDraft({ reasoning: null, tool_calls: [], final_text: "x" })).toBe(false);
    expect(
      isEmptyDraft({ reasoning: null, tool_calls: [{ id: "a", name: "b", arguments: {} }], final_text: null }),
    ).toBe(false);
    expect(isEmptyDraft({ reasoning: "r", tool_calls: [], final_text: null })).toBe(false);
  });
});

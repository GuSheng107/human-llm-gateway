// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "../../api/client";
import {
  acknowledgeToolCallWarning,
  generateDraft,
  generateToolArguments,
  getRequestView,
  getRequestViewBlock,
} from "../../api/tasks";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("tasks api（R4 新契约）", () => {
  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, "test-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({}))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("getRequestView 请求 request-view 端点", async () => {
    await getRequestView("9");
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/tasks/9/request-view");
    expect(init?.method).toBeUndefined();
  });

  it("getRequestViewBlock 请求块完整内容端点", async () => {
    await getRequestViewBlock("9", "ctx_1_blk_2");
    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/tasks/9/request-view/blocks/ctx_1_blk_2");
  });

  it("acknowledgeToolCallWarning 发送 POST 与空 body", async () => {
    await acknowledgeToolCallWarning("9");
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/tasks/9/tool-call-warning/acknowledge");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({});
  });

  it("generateDraft 发送新契约字段（稳定 ctx ID / system / 附件开关）", async () => {
    await generateDraft("9", {
      llm_config_id: 3,
      mode: "both",
      generation_instruction: "用中文",
      include_caller_system: false,
      excluded_context_item_ids: ["ctx_2"],
      include_attachments: true,
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/tasks/9/drafts/generate");
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      llm_config_id: 3,
      mode: "both",
      generation_instruction: "用中文",
      include_caller_system: false,
      excluded_context_item_ids: ["ctx_2"],
      include_attachments: true,
    });
    // 旧契约字段不得再发送
    expect(body.exclude_context_indices).toBeUndefined();
    expect(body.guidance).toBeUndefined();
    expect(body.selected_tool_names).toBeUndefined();
  });

  it("generateToolArguments 按工具名生成参数", async () => {
    await generateToolArguments("9", "search", {
      llm_config_id: 3,
      current_arguments: { q: "" },
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/tasks/9/tools/search/arguments/generate");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body.current_arguments).toEqual({ q: "" });
  });
});

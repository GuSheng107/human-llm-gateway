// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { TOKEN_KEY } from "../../api/client";
import { LogDetailModal } from "./LogDetailModal";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const detailBody = {
  id: "app-12",
  kind: "app",
  category: "llm_forward",
  level: "info",
  event: "llm.upstream.request",
  message: "开始调用上游 LLM",
  username: "alice",
  user_id: "1",
  request_id: "trace-abc",
  task_id: "7",
  api_key_id: null,
  connection_id: null,
  context: { duration_ms: 812, status_code: 200 },
  detail: {
    schema_version: 1,
    category: "llm_forward",
    sections: [
      {
        key: "conversion",
        title: "转换说明",
        format: "json",
        data: { inbound_protocol: "openai_chat", real_model: "gpt-4o" },
        redacted_fields: ["api_key"],
      },
      {
        key: "upstream_stream",
        title: "上游流事件",
        format: "jsonl",
        data: '{"text":"hello"}\n{"text":"world"}',
      },
    ],
    links: [{ kind: "task", id: "7", label: "任务 TASK001" }],
  },
  detail_size_bytes: 2048,
  detail_truncated: false,
  created_at: "2026-09-07T08:00:00Z",
};

describe("LogDetailModal", () => {
  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, "test-token");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/logs/app-12") {
          return Promise.resolve(jsonResponse(detailBody));
        }
        return Promise.reject(new Error(`unexpected fetch: ${url}`));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("懒加载详情并渲染 sections、脱敏徽章与关联链接", async () => {
    render(
      <LogDetailModal entryId="app-12" title="应用 · llm.upstream.request" onClose={() => {}} />,
    );
    await waitFor(() => {
      expect(screen.getByText("转换说明")).toBeTruthy();
    });
    expect(screen.getByText("上游流事件")).toBeTruthy();
    expect(screen.getByText(/脱敏 1 字段/)).toBeTruthy();
    expect(screen.getByText(/2\.0 KB/)).toBeTruthy();
    // json section 渲染为格式化 JSON
    expect(screen.getByText(/gpt-4o/)).toBeTruthy();
    // jsonl section 渲染原始行
    expect(screen.getByText(/"hello"/)).toBeTruthy();
  });

  it("详情大小超限显示截断标记", async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(
        jsonResponse({ ...detailBody, detail_truncated: true, detail: null }),
      ),
    );
    render(<LogDetailModal entryId="app-12" onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/已截断/)).toBeTruthy();
    });
    expect(screen.getByText(/没有详情信封/)).toBeTruthy();
  });

  it("加载失败显示错误横幅", async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: "not found" }), { status: 404 }),
      ),
    );
    render(<LogDetailModal entryId="app-12" onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/请求失败/)).toBeTruthy();
    });
  });
});

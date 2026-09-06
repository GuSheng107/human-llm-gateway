// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "../../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { ModelsPage } from "./ModelsPage";

const models = Array.from({ length: 35 }, (_, index) => ({
  id: String(index + 1),
  scope: "system",
  owner_user_id: null,
  model_id: `model-${index + 1}`,
  display_name: `模型 ${index + 1}`,
  owned_by: "gateway",
  description: null,
  sort_order: index,
  is_enabled: true,
  input_price_per_million: null,
  output_price_per_million: null,
  cached_input_price_per_million: null,
  cached_write_price_per_million: null,
  context_window: 128000,
  max_output_tokens: 8192,
  capabilities: ["streaming"],
  billing_tier: "free",
  endpoint_types: ["openai_chat"],
  logo_url: null,
  tags: ["通用"],
  created_at: "2026-01-01T00:00:00Z",
}));

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ModelsPage 分页交互", () => {
  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, "test-token");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/auth/me") {
          return Promise.resolve(
            jsonResponse({
              id: "1",
              username: "admin",
              display_name: "管理员",
              role: "admin",
              must_change_password: false,
              capabilities: ["model.manage"],
              email: null,
              avatar_base64: null,
            }),
          );
        }
        if (url.startsWith("/api/fake-models?")) {
          const query = new URL(url, "http://localhost").searchParams;
          const page = Number(query.get("page"));
          const pageSize = Number(query.get("page_size"));
          const start = (page - 1) * pageSize;
          return Promise.resolve(
            jsonResponse({
              items: models.slice(start, start + pageSize),
              page,
              page_size: pageSize,
              total: models.length,
            }),
          );
        }
        if (url.startsWith("/api/model-groups?")) {
          return Promise.resolve(jsonResponse({ items: [], page: 1, page_size: 100, total: 0 }));
        }
        return Promise.reject(new Error(`unexpected fetch: ${url}`));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("默认每页 10 条并可切换页码和每页数量", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ModelsPage />
      </AuthProvider>,
    );

    await screen.findByText("model-10");
    expect(screen.queryByText("model-11")).toBeNull();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("model-11")).toBeTruthy();

    await user.selectOptions(screen.getByRole("combobox", { name: "每页条数" }), "20");
    await waitFor(() => expect(screen.getByText("model-1")).toBeTruthy());
    expect(screen.queryByText("model-21")).toBeNull();

    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("page=2&page_size=10"),
      ),
    ).toBe(true);
  });
});

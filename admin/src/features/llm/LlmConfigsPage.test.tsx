// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "../../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { LlmConfigsPage } from "./LlmConfigsPage";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("管理员 LLM 配置只读视角", () => {
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
              capabilities: ["logs.manage"],
              email: null,
              avatar_base64: null,
            }),
          );
        }
        if (url.startsWith("/api/llm-configs?")) {
          return Promise.resolve(jsonResponse({ items: [], page: 1, page_size: 20, total: 0 }));
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

  it("禁用创建按钮且点击不会发送写请求", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <LlmConfigsPage />
      </AuthProvider>,
    );

    expect(await screen.findByText(/管理员视角 · 只读/)).toBeTruthy();
    const create = screen.getByRole("button", { name: "新建配置" });
    expect((create as HTMLButtonElement).disabled).toBe(true);
    await user.click(create);
    expect(screen.queryByRole("dialog", { name: "新建 LLM 配置" })).toBeNull();
    expect(vi.mocked(fetch).mock.calls).toHaveLength(2);
  });
});

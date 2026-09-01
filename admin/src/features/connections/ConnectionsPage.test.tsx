// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "../../api/client";
import type { ImConnection, PlatformSpec } from "../../types/gateway";
import { AuthProvider } from "../auth/AuthContext";
import { ConnectionsPage } from "./ConnectionsPage";

const platforms: PlatformSpec[] = [
  {
    code: "http_poll",
    label: "自定义 HTTP 轮询",
    description: "按 cursor 拉取任务。",
    kind: "server",
    supports_delivery: false,
    supports_login: false,
    requires_binding: false,
    binding_command: null,
    config_schema: [],
  },
  {
    code: "webhook",
    label: "自定义 Webhook",
    description: "接收入站消息。",
    kind: "server",
    supports_delivery: true,
    supports_login: false,
    requires_binding: false,
    binding_command: "connect webhook",
    config_schema: [],
  },
  {
    code: "websocket",
    label: "自定义 WebSocket",
    description: "双向 WebSocket 会话。",
    kind: "server",
    supports_delivery: true,
    supports_login: false,
    requires_binding: false,
    binding_command: "connect websocket",
    config_schema: [],
  },
  {
    code: "wecom_aibot",
    label: "企业微信智能机器人",
    description: "企微 WebSocket 长连接。",
    kind: "client",
    supports_delivery: true,
    supports_login: false,
    requires_binding: true,
    binding_command: "connect mycom",
    config_schema: [],
  },
  {
    code: "wecom_ilink",
    label: "微信 iLink",
    description: "微信扫码连接。",
    kind: "client",
    supports_delivery: true,
    supports_login: true,
    requires_binding: true,
    binding_command: null,
    config_schema: [],
  },
];

function connection(
  id: string,
  name: string,
  platform: string,
  overrides: Partial<ImConnection> = {},
): ImConnection {
  const spec = platforms.find((item) => item.code === platform);
  return {
    id,
    name,
    platform,
    platform_label: spec?.label ?? platform,
    state: "stopped",
    desired_running: false,
    bound: false,
    owner_user_id: null,
    owner_username: null,
    config: {},
    last_error_code: null,
    last_error_message: null,
    retry_count: 0,
    next_retry_at: null,
    last_health_at: null,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ConnectionsPage 平台分区与启用校验", () => {
  let connections: ImConnection[];

  beforeEach(() => {
    connections = [
      connection("1", "个人微信", "wecom_ilink"),
      connection("2", "企微值班号", "wecom_aibot"),
      connection("3", "轮询接入", "http_poll"),
    ];
    localStorage.setItem(TOKEN_KEY, "test-token");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url === "/api/auth/me") {
          return Promise.resolve(jsonResponse({
            id: "1",
            username: "user",
            display_name: "普通用户",
            role: "user",
            must_change_password: false,
            capabilities: [],
            email: null,
            avatar_base64: null,
          }));
        }
        if (url === "/api/im-platforms") return Promise.resolve(jsonResponse(platforms));
        if (url === "/api/im-connections?page=1&page_size=100") {
          return Promise.resolve(jsonResponse({
            items: connections,
            page: 1,
            page_size: 100,
            total: connections.length,
          }));
        }
        if (url === "/api/im-connections" && method === "POST") {
          const payload = JSON.parse(String(init?.body)) as {
            name: string;
            platform: string;
            config: Record<string, unknown>;
          };
          const created = connection("4", payload.name, payload.platform);
          connections = [...connections, created];
          return Promise.resolve(jsonResponse(created, 201));
        }
        if (url === "/api/im-connections/4/login" && method === "POST") {
          return Promise.resolve(jsonResponse({
            qrcode: "qr-code",
            qrcode_img_content: "cXItY29kZQ==",
          }));
        }
        if (url === "/api/im-connections/4/login" && method === "GET") {
          return Promise.resolve(jsonResponse({ status: "wait", bound: false }));
        }
        if (url === "/api/im-connections/2/binding" && method === "POST") {
          return Promise.resolve(jsonResponse({
            binding_code: "connect mycom",
            expires_at: "2026-09-01T00:05:00Z",
          }));
        }
        if (url === "/api/im-connections/2/binding/status") {
          return Promise.resolve(jsonResponse({
            bound: false,
            binding_pending: true,
            binding_expires_at: "2026-09-01T00:05:00Z",
          }));
        }
        if (url === "/api/im-connections/2/start" && method === "POST") {
          connections = connections.map((item) =>
            item.id === "2" ? { ...item, desired_running: true, state: "starting" } : item,
          );
          return Promise.resolve(jsonResponse(connections.find((item) => item.id === "2")));
        }
        return Promise.reject(new Error(`unexpected fetch: ${method} ${url}`));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("每个平台只展示一行且不显示新增、搜索和分页", async () => {
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    expect(await screen.findByText("微信 iLink")).toBeTruthy();
    expect(screen.getByText("企业微信智能机器人")).toBeTruthy();
    expect(screen.getByText("自定义 HTTP 轮询")).toBeTruthy();
    expect(screen.getByText("自定义 Webhook")).toBeTruthy();
    expect(screen.getByText("自定义 WebSocket")).toBeTruthy();
    expect(screen.queryByText("个人微信")).toBeNull();
    expect(screen.queryByText("企微值班号")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加" })).toBeNull();
    expect(screen.queryByText(/新建.*连接/)).toBeNull();
    expect(screen.queryByPlaceholderText("搜索连接名称")).toBeNull();
    expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
  });

  it("微信未配置时点击绑定直接创建连接并打开二维码", async () => {
    connections = connections.filter((item) => item.platform !== "wecom_ilink");
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    await screen.findByText("微信 iLink");
    await user.click(screen.getByRole("button", { name: "绑定（扫码）" }));
    expect(await screen.findByAltText("登录二维码")).toBeTruthy();
    expect(screen.queryByText("连接名称")).toBeNull();

    const createCall = vi.mocked(fetch).mock.calls.find(
      ([url, init]) => String(url) === "/api/im-connections" && init?.method === "POST",
    );
    expect(createCall).toBeTruthy();
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      name: "微信 iLink",
      platform: "wecom_ilink",
      config: {},
    });
  });

  it("企微未绑定时不启用并进入固定命令绑定流程", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    await screen.findByText("企业微信智能机器人");
    await user.click(screen.getByRole("switch", { name: "企业微信智能机器人启用" }));
    expect(await screen.findByText("connect mycom")).toBeTruthy();

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/2/binding"))).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/2/start"))).toBe(false);
  });

  it("企微已绑定后允许启用", async () => {
    connections = connections.map((item) =>
      item.id === "2" ? { ...item, bound: true } : item,
    );
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    await screen.findByText("企业微信智能机器人");
    await user.click(screen.getByRole("switch", { name: "企业微信智能机器人启用" }));
    await waitFor(() => {
      expect(
        vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/2/start")),
      ).toBe(true);
    });
  });
});

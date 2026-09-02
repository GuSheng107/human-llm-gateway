// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
    config_schema: [
      {
        name: "pull_token",
        label: "拉取 Token",
        type: "string",
        required: true,
        secret: true,
        description: "",
        credential_kind: "gateway_token",
        auto_generate: true,
      },
    ],
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
    config_schema: [
      {
        name: "inbound_token",
        label: "入站 Token",
        type: "string",
        required: true,
        secret: true,
        description: "",
        credential_kind: "gateway_token",
        auto_generate: true,
      },
      {
        name: "outbound_url",
        label: "推送 URL",
        type: "url",
        required: true,
        secret: false,
        description: "",
      },
    ],
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
    config_schema: [
      {
        name: "connection_token",
        label: "连接 Token",
        type: "string",
        required: true,
        secret: true,
        description: "",
        credential_kind: "gateway_token",
        auto_generate: true,
      },
    ],
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
    config_schema: [
      {
        name: "bot_id",
        label: "Bot ID",
        type: "string",
        required: true,
        secret: false,
        description: "",
      },
      {
        name: "secret",
        label: "Bot Secret",
        type: "string",
        required: true,
        secret: true,
        description: "",
      },
    ],
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
      connection("3", "轮询接入", "http_poll", {
        config: { pull_token: null, pull_token_set: true },
      }),
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
          const tokenField =
            payload.platform === "http_poll"
              ? "pull_token"
              : payload.platform === "webhook"
                ? "inbound_token"
                : payload.platform === "websocket"
                  ? "connection_token"
                  : null;
          const token = `hllm-${"a".repeat(43)}`;
          const created = connection("4", payload.name, payload.platform, {
            config: tokenField ? { [tokenField]: null, [`${tokenField}_set`]: true } : {},
            generated_tokens: tokenField ? { [tokenField]: token } : null,
          });
          connections = [...connections, { ...created, generated_tokens: null }];
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
        if (url === "/api/im-connections/2/binding/cancel" && method === "POST") {
          return Promise.resolve(jsonResponse(connection("2", "企微值班号", "wecom_aibot")));
        }
        if (url === "/api/im-connections/4/binding" && method === "POST") {
          return Promise.resolve(jsonResponse({
            binding_code: "connect websocket",
            expires_at: "2026-09-01T00:05:00Z",
          }));
        }
        if (url === "/api/im-connections/4/binding/status") {
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
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/2/binding"))).toBe(true);
    });
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

  it("关闭扫码弹窗不删除连接与已保存凭据", async () => {
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
    await user.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => {
      expect(screen.queryByAltText("登录二维码")).toBeNull();
    });
    // 关闭弹窗后不产生 DELETE 请求（方案2：不删除连接与凭据）。
    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).startsWith("/api/im-connections/") && init?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("企微绑定弹窗提供取消本次绑定监听且不删除连接", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    await screen.findByText("企业微信智能机器人");
    await user.click(screen.getByRole("switch", { name: "企业微信智能机器人启用" }));
    expect(await screen.findByText("connect mycom")).toBeTruthy();
    // 显式取消绑定监听：调用 binding/cancel 端点。
    await user.click(await screen.findByRole("button", { name: "取消本次绑定监听" }));
    await waitFor(() => {
      expect(
        vi.mocked(fetch).mock.calls.some(([url, init]) =>
          String(url).endsWith("/2/binding/cancel") && init?.method === "POST",
        ),
      ).toBe(true);
    });
    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).startsWith("/api/im-connections/") && init?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("已有 HTTP 连接在统一弹窗同屏展示配置、完整 URL 和 curl", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    const title = await screen.findByRole("heading", { name: "自定义 HTTP 轮询" });
    const panel = title.closest("section");
    expect(panel).toBeTruthy();
    await user.click(within(panel as HTMLElement).getByRole("button", { name: "配置" }));

    expect(await screen.findByRole("dialog", { name: "自定义 HTTP 轮询配置与接入" })).toBeTruthy();
    expect(screen.getByText("连接配置")).toBeTruthy();
    expect(screen.getByText("接入指引")).toBeTruthy();
    expect(screen.getByText("完整 URL 地址")).toBeTruthy();
    expect(screen.getByText("curl 命令")).toBeTruthy();
    expect(screen.getAllByText(/\/connectors\/http\/3\/tasks/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("textbox", { name: "拉取 Token" })).toBeNull();
    expect(screen.queryByText(/需要绑定的平台/)).toBeNull();
  });

  it("系统生成 Token 仅本次展示，重开后只允许重新生成", async () => {
    const token = `hllm-${"a".repeat(43)}`;
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
      </AuthProvider>,
    );

    const title = await screen.findByRole("heading", { name: "自定义 WebSocket" });
    const panel = title.closest("section");
    expect(panel).toBeTruthy();
    await user.click(within(panel as HTMLElement).getByRole("button", { name: "配置" }));
    await user.click(await screen.findByRole("button", { name: "保存并生成接入信息" }));

    expect(await screen.findByText(token)).toBeTruthy();
    expect(screen.getByText("明文仅本次展示，关闭窗口后无法再次查看。")).toBeTruthy();
    expect(screen.getAllByText(/\/connectors\/ws\/4\?token=hllm-/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByText(token)).toBeNull());

    const reopenedTitle = await screen.findByRole("heading", { name: "自定义 WebSocket" });
    const reopenedPanel = reopenedTitle.closest("section");
    await user.click(within(reopenedPanel as HTMLElement).getByRole("button", { name: "配置" }));
    expect(await screen.findByText("已配置，明文不可再次查看。")).toBeTruthy();
    expect(screen.queryByText(token)).toBeNull();
    expect(screen.getByRole("button", { name: "重新生成" })).toBeTruthy();
  });

  it("初次加载未结束前显示骨架屏，渲染完成才展示平台面板", async () => {
    let resolveConnections!: (value: unknown) => void;
    let resolvePlatforms!: (value: unknown) => void;
    vi.mocked(fetch).mockImplementation((input, init) => {
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
      if (url === "/api/im-platforms") {
        return new Promise((resolve) => {
          resolvePlatforms = (value) => resolve(jsonResponse(value));
        });
      }
      if (url === "/api/im-connections?page=1&page_size=100") {
        return new Promise((resolve) => {
          resolveConnections = (value) => resolve(jsonResponse(value));
        });
      }
      return Promise.reject(new Error(`unexpected fetch: ${method} ${url}`));
    });

    render(
      <AuthProvider>
        <ConnectionsPage />
     </AuthProvider>,
    );

    expect(screen.getByRole("status", { name: "正在加载连接" })).toBeTruthy();
    expect(screen.queryByText("微信 iLink")).toBeNull();
    expect(screen.queryByText("企业微信智能机器人")).toBeNull();

    resolvePlatforms(platforms);
    resolveConnections({ items: connections, page: 1, page_size: 100, total: connections.length });

    expect(await screen.findByText("微信 iLink")).toBeTruthy();
    expect(screen.getByText("企业微信智能机器人")).toBeTruthy();
    expect(screen.queryByRole("status", { name: "正在加载连接" })).toBeNull();
  });

  it("启用中的连接不允许删除：删除按钮被禁用", async () => {
    connections = connections.map((item) =>
      item.id === "2" ? { ...item, bound: true, desired_running: true, state: "running" } : item,
    );
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
    </AuthProvider>,
    );

    const title = await screen.findByRole("heading", { name: "企业微信智能机器人" });
    const panel = title.closest("section") as HTMLElement;
    const deleteButton = within(panel).getByRole("button", { name: "删除" }) as HTMLButtonElement;
    expect(deleteButton.disabled).toBe(true);
    expect(deleteButton.title).toBe("请先关闭连接后再删除");

    await user.click(deleteButton);
    const fetchMock = vi.mocked(fetch);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).startsWith("/api/im-connections/") && init?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("未启用的连接可以正常触发删除流程", async () => {
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <ConnectionsPage />
    </AuthProvider>,
    );

    const title = await screen.findByRole("heading", { name: "企业微信智能机器人" });
    const panel = title.closest("section") as HTMLElement;
    const deleteButton = within(panel).getByRole("button", { name: "删除" }) as HTMLButtonElement;
    expect(deleteButton.disabled).toBe(false);
    expect(deleteButton.title).toBe("");
  });
});

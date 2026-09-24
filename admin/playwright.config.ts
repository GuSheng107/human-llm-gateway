import { defineConfig, devices } from "@playwright/test";

/**
 * Human LLM Gateway 管理后台 E2E 测试配置。
 *
 * - 复用本地 Chrome（channel: "chrome"），不额外下载 Chromium。
 * - 复用已运行的前端 dev server，默认 http://127.0.0.1:5176（本机
 *   5173-5175 被其他项目占用时 vite 会自动落到 5176）。
 *   Vite 已把 /api、/v1、/connectors 代理到后端 http://127.0.0.1:8000。
 * - 登录验证码为图形验证码，无法自动化；登录态通过 storageState 复用，
 *   首次运行用 `npm run e2e:login` 人工输入验证码保存会话。
 * - 演示模式（demo 项目）开启录屏，输出 webm 到 test-results/，
 *   用于制作演示视频素材。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5176",
    channel: "chrome",
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      // 常规 E2E：复用已保存的登录态，跑全链路断言。
      name: "chrome",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        storageState: "e2e/.auth/admin.json",
      },
    },
    {
      // 演示录屏：同样复用登录态，但 headless=false 且开启录屏。
      name: "demo",
      testMatch: /demo\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        headless: false,
        storageState: "e2e/.auth/admin.json",
        video: { mode: "on", size: { width: 1440, height: 900 } },
      },
    },
  ],
});

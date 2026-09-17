import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * 登录态保存脚本（手动验证码）：
 *
 *   npm run e2e:login
 *
 * 打开本地 Chrome，人工输入账号/密码/验证码完成登录，脚本检测到
 * localStorage 中出现 hlg_admin_token 后自动保存 storageState 到
 * e2e/.auth/admin.json，供后续 E2E 复用，无需重复输验证码。
 */
const ADMIN_USERNAME = process.env.ADMIN_USERNAME ?? "admin";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "Admin-Test!2026";
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5176";
const OUT_DIR = path.join(process.cwd(), "e2e", ".auth");
const OUT_FILE = path.join(OUT_DIR, "admin.json");

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: false });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  await page.goto(`${BASE_URL}/login`);

  // 预填账号密码，验证码留给用户手动输入。
  await page.getByPlaceholder("登录账号").fill(ADMIN_USERNAME);
  await page.getByPlaceholder("账号密码").fill(ADMIN_PASSWORD);

  console.log("======================================================");
  console.log("请在浏览器中查看验证码图片并输入验证码，然后点击「登录」。");
  console.log("登录成功后脚本会自动保存会话并退出。");
  console.log("======================================================");

  // 等待 localStorage 出现 token（登录成功），最多 5 分钟。
  await page.waitForFunction(
    () => localStorage.getItem("hlg_admin_token") !== null,
    { timeout: 300_000 },
  );

  // 等待跳转到控制台，确保会话稳定。
  await page.waitForURL(/\/console/, { timeout: 30_000 }).catch(() => {});

  fs.mkdirSync(OUT_DIR, { recursive: true });
  await context.storageState({ path: OUT_FILE });
  console.log(`\n✅ 登录态已保存到 ${OUT_FILE}`);

  await browser.close();
}

main().catch((err) => {
  console.error("登录保存失败：", err);
  process.exit(1);
});
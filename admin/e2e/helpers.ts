import { APIRequestContext, expect } from "@playwright/test";

/**
 * 全链路 E2E 的 API 辅助：借 Playwright 的 request context（复用页面
 * 的 cookie/localStorage 无法直接用于 API），改为直接用登录态 token。
 * 这里用「浏览器登录后从 localStorage 读取 token」的方式驱动管理 API。
 */

export const E2E_BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5176";

/** 从已登录页面的 localStorage 读取管理台会话 token。 */
export async function readAdminToken(page: import("@playwright/test").Page): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem("hlg_admin_token"));
  if (!token) throw new Error("localStorage 中没有 hlg_admin_token，登录态失效");
  return token;
}

/** 用管理台 token 构造一个带 Authorization 头的 request context。 */
export function adminApiContext(
  playwright: import("@playwright/test").Playwright,
  token: string,
): Promise<APIRequestContext> {
  return playwright.request.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}

/** 创建管理员系统 Fake Model，返回其数字 id 与 model_id。 */
export async function createFakeModel(
  api: APIRequestContext,
  modelId: string,
): Promise<{ id: string; modelId: string }> {
  const resp = await api.post("/api/fake-models", {
    data: {
      model_id: modelId,
      display_name: `E2E ${modelId}`,
      endpoint_types: ["openai_chat"],
      enabled: true,
    },
  });
  expect(resp.ok(), `创建 Fake Model 失败: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  return { id: String(body.id), modelId: body.model_id };
}

/** 删除 Fake Model（清理）。 */
export async function deleteFakeModel(api: APIRequestContext, id: string): Promise<void> {
  await api.delete(`/api/fake-models/${id}`);
}

/** 创建 API Key（human 策略），返回完整明文 key。 */
export async function createApiKey(
  api: APIRequestContext,
  name: string,
  fakeModelIds: string[],
): Promise<{ id: string; plaintext: string }> {
  const resp = await api.post("/api/api-keys", {
    data: {
      name,
      delivery_mode: "web",
      reply_strategy: "human",
      fake_model_ids: fakeModelIds.map((x) => Number(x)),
    },
  });
  expect(resp.ok(), `创建 API Key 失败: ${resp.status()} ${await resp.text()}`).toBeTruthy();
  const body = await resp.json();
  return { id: String(body.id), plaintext: body.plaintext };
}

/** 删除 API Key（清理）。 */
export async function deleteApiKey(api: APIRequestContext, id: string): Promise<void> {
  await api.delete(`/api/api-keys/${id}`);
}

/**
 * 用 API Key 发起一次 OpenAI Chat 推理请求（human 策略会挂起直到人工回复）。
 * 返回 { taskId, promise }：promise 在人工回复后才 resolve 出完整响应 status + body。
 */
export function startInference(
  apiKey: string,
  modelId: string,
  prompt = "E2E 全链路测试：请回复 OK",
): { promise: Promise<{ status: number; body: string; headers: Record<string, string> }> } {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);
  const promise = fetch(`${E2E_BASE_URL}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: modelId,
      messages: [{ role: "user", content: prompt }],
      stream: false,
    }),
    signal: controller.signal,
  }).then(async (resp) => {
    clearTimeout(timeout);
    const body = await resp.text();
    const headers: Record<string, string> = {};
    resp.headers.forEach((v, k) => (headers[k] = v));
    return { status: resp.status, body, headers };
  });

  return { promise };
}
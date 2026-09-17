import { test, expect } from "@playwright/test";
import {
  adminApiContext,
  createApiKey,
  createFakeModel,
  deleteApiKey,
  deleteFakeModel,
  readAdminToken,
  startInference,
} from "./helpers";

/**
 * 全链路 E2E：管理员视角的「真人驱动的模型兼容网关」闭环。
 *
 *   创建 Fake Model → 创建 API Key → 调用方发起 OpenAI 推理请求
 *   → Web 回复工作台人工回复 → 验证调用方收到 Fake Model 身份的返回。
 *
 * 前置：后端 http://127.0.0.1:8000 与前端 http://127.0.0.1:5173 已运行；
 *       已用 `npm run e2e:login` 保存登录态（e2e/.auth/admin.json）。
 */
test.describe("全链路：人工回复闭环", () => {
  test("创建模型 → API Key → 推理 → 工作台回复 → 验证 Fake Model 返回", async ({
    page,
    playwright,
  }) => {
    const suffix = Date.now().toString(36);
    const fakeModelId = `e2e-gpt-${suffix}`;
    const expectedReply = `E2E 全链路人工回复 ${suffix}`;

    // 1. 进入控制台（storageState 已登录，应直接进入 /console）。
    await page.goto("/console");
    await expect(page.locator("body")).toContainText(/控制台|工作台|console/i, {
      timeout: 20_000,
    });

    // 2. 用登录态 token 构造管理 API 客户端。
    const token = await readAdminToken(page);
    const api = await adminApiContext(playwright, token);
    const model = await createFakeModel(api, fakeModelId);
    const apiKey = await createApiKey(api, `e2e-key-${suffix}`, [model.id]);

    // 3. 后台发起推理请求（human 策略会挂起，直到人工回复）。
    const inference = startInference(apiKey.plaintext, model.modelId);

    // 4. 在回复工作台找到并回复任务。
    try {
      await page.goto("/replies");
      await page.getByRole("button", { name: "刷新" }).click();

      // 收件箱里应出现以该模型命名的任务。
      const inboxItem = page.locator("li").filter({ hasText: fakeModelId }).first();
      await expect(inboxItem).toBeVisible({ timeout: 30_000 });
      await inboxItem.click();

      // 打开回复编辑器。
      await page.getByRole("button", { name: "回复", exact: true }).click();

      // 填写最终回复。
      const replyBox = page.getByPlaceholder("面向调用方的最终回复内容");
      await expect(replyBox).toBeVisible({ timeout: 15_000 });
      await replyBox.fill(expectedReply);

      // 提交 → 确认。
      await page.getByRole("button", { name: "提交回复" }).click();
      await page.getByRole("button", { name: "确认提交" }).click();

      // 等待回复提交成功提示（或收件箱刷新后任务消失）。
      await expect(page.getByText("回复已提交")).toBeVisible({ timeout: 20_000 });

      // 5. 等待推理请求返回并校验 Fake Model 身份。
      const result = await inference.promise;
      expect(result.status).toBe(200);
      const body = JSON.parse(result.body);
      expect(body.model).toBe(fakeModelId);
      expect(body.choices?.[0]?.message?.content).toContain(expectedReply);
    } finally {
      // 清理测试数据。
      await deleteApiKey(api, apiKey.id);
      await deleteFakeModel(api, model.id);
      await api.dispose();
    }
  });
});
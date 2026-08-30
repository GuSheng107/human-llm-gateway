import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentEditBridge, registerEditBridge } from "./bridge";
import {
  CONTEXT_VERSIONS,
  buildContextSnapshot,
  featureForRoute,
} from "./contextRegistry";

describe("bridge", () => {
  it("默认无桥；注册/注销往返", () => {
    expect(currentEditBridge()).toBeNull();
    registerEditBridge({
      getDraft: () => null,
      getResource: () => ({}),
      apply: () => {},
    });
    expect(currentEditBridge()).not.toBeNull();
    registerEditBridge(null);
    expect(currentEditBridge()).toBeNull();
  });

  it("后注册覆盖先注册（多个编辑器竞争时取最新）", () => {
    registerEditBridge({
      getDraft: () => ({ reasoning: null, final_text: "first", tool_calls: [] }),
      getResource: () => ({ task_id: "1" }),
      apply: () => {},
    });
    registerEditBridge({
      getDraft: () => ({ reasoning: null, final_text: "second", tool_calls: [] }),
      getResource: () => ({ task_id: "2" }),
      apply: () => {},
    });
    expect(currentEditBridge()?.getDraft()?.final_text).toBe("second");
    expect(currentEditBridge()?.getResource()["task_id"]).toBe("2");
    registerEditBridge(null);
  });
});

describe("featureForRoute", () => {
  it("已注册路由返回 feature", () => {
    expect(featureForRoute("/console")).toBe("console");
    expect(featureForRoute("/tasks")).toBe("task_list");
    expect(featureForRoute("/api-keys")).toBe("api_keys");
    expect(featureForRoute("/llm-configs")).toBe("llm_configs");
    expect(featureForRoute("/connections")).toBe("connections");
    expect(featureForRoute("/models")).toBe("models");
    expect(featureForRoute("/settings/invitations")).toBe("invitations");
    expect(featureForRoute("/settings/users")).toBe("users");
    expect(featureForRoute("/settings/account")).toBe("account");
  });

  it("未注册路由返回 null（不发送上下文）", () => {
    expect(featureForRoute("/login")).toBeNull();
    expect(featureForRoute("/register")).toBeNull();
    expect(featureForRoute("/unknown/page")).toBeNull();
  });
});

describe("buildContextSnapshot", () => {
  beforeEach(() => {
    registerEditBridge(null);
  });
  afterEach(() => {
    registerEditBridge(null);
  });

  it("task_list 提取 state/search 白名单字段", () => {
    const snapshot = buildContextSnapshot("/tasks", "?state=waiting_human&search=abc");
    expect(snapshot).not.toBeNull();
    expect(snapshot!.feature).toBe("task_list");
    expect(snapshot!.resource).toEqual({ state_filter: "waiting_human", search: "abc" });
    expect(snapshot!.context_version).toBe(CONTEXT_VERSIONS["task_list"]);
    expect(snapshot!.unsaved_edit).toBeNull();
  });

  it("无编辑器桥时不带 unsaved_edit", () => {
    const snapshot = buildContextSnapshot("/api-keys", "");
    expect(snapshot!.feature).toBe("api_keys");
    expect(snapshot!.unsaved_edit).toBeNull();
    expect(snapshot!.resource).toEqual({});
  });

  it("编辑器桥注入草稿与任务资源字段", () => {
    registerEditBridge({
      getDraft: () => ({
        reasoning: "思路",
        final_text: "草稿",
        tool_calls: [{ id: "c1", name: "fn", arguments: { a: 1 } }],
      }),
      getResource: () => ({ task_id: "9", state: "waiting_human", model: "deepseek-v4-pro" }),
      apply: () => {},
    });
    const snapshot = buildContextSnapshot("/tasks", "");
    expect(snapshot!.unsaved_edit?.final_text).toBe("草稿");
    expect(snapshot!.unsaved_edit?.tool_calls).toHaveLength(1);
    expect(snapshot!.resource["task_id"]).toBe("9");
    expect(snapshot!.resource["state"]).toBe("waiting_human");
  });

  it("secret 形态字段不会由注册表采集（白名单只含声明键）", () => {
    // 恶意查询串包含疑似密钥：state/search 之外的字段一律不进入快照
    const snapshot = buildContextSnapshot(
      "/tasks",
      "?state=x&password=hlg_secret&api_key=sk-1234567890abcdefgh",
    );
    expect(snapshot!.resource).toEqual({ state_filter: "x" });
    expect(JSON.stringify(snapshot)).not.toContain("hlg_secret");
    expect(JSON.stringify(snapshot)).not.toContain("sk-1234567890");
  });
});

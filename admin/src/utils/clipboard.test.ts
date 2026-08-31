import { afterEach, describe, expect, it, vi } from "vitest";
import { notify } from "../components/feedback/Toast";
import { copyText } from "./clipboard";

vi.mock("../components/feedback/Toast", () => ({ notify: vi.fn() }));

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("copyText", () => {
  it("优先使用 Clipboard API", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyText("abc", "模型 ID")).resolves.toEqual({
      ok: true,
      reason: "clipboard",
    });
    expect(writeText).toHaveBeenCalledWith("abc");
    expect(notify).toHaveBeenCalledWith("已复制 模型 ID", "success");
  });

  it("Clipboard API 失败时使用 textarea", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    const textarea = {
      value: "",
      style: {} as CSSStyleDeclaration,
      setAttribute: vi.fn(),
      focus: vi.fn(),
      select: vi.fn(),
      remove: vi.fn(),
    };
    vi.stubGlobal("document", {
      createElement: vi.fn(() => textarea),
      body: { appendChild: vi.fn() },
      execCommand: vi.fn(() => true),
    });

    await expect(copyText("fallback")).resolves.toEqual({
      ok: true,
      reason: "exec-command",
    });
    expect(textarea.value).toBe("fallback");
  });

  it("两条路径都失败时给出错误 toast", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", {
      createElement: vi.fn(() => {
        throw new Error("unavailable");
      }),
    });

    await expect(copyText("abc")).resolves.toEqual({
      ok: false,
      reason: "unavailable",
    });
    expect(notify).toHaveBeenCalledWith("复制失败，请手动选择", "error");
  });
});

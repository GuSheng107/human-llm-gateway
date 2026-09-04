import { describe, expect, it } from "vitest";
import { buildInitialArguments } from "./toolArguments";

describe("buildInitialArguments", () => {
  it("只根据 JSON Schema 生成必填字段和显式默认值", () => {
    expect(
      buildInitialArguments({
        type: "object",
        properties: {
          command: { type: "string" },
          path: { type: "string", default: "" },
          mode: { type: "string", enum: ["safe", "fast"] },
          count: { type: "integer" },
        },
        required: ["command", "count"],
      }),
    ).toEqual({ command: "", path: "", mode: "safe", count: 0 });
  });

  it("不会凭空添加可选业务参数", () => {
    expect(
      buildInitialArguments({
        type: "object",
        properties: { city: { type: "string" }, limit: { type: "integer" } },
      }),
    ).toEqual({});
  });
});

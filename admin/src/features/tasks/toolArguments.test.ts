import { describe, expect, it } from "vitest";
import { buildInitialArguments } from "./toolArguments";

describe("buildInitialArguments", () => {
  it("根据 JSON Schema 生成全部字段并保留显式初始值", () => {
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

  it("为可选字段生成可直接编辑的占位值", () => {
    expect(
      buildInitialArguments({
        type: "object",
        properties: { city: { type: "string" }, limit: { type: "integer" } },
      }),
    ).toEqual({ city: "", limit: 0 });
  });

  it("递归展开对象与 allOf 属性", () => {
    expect(
      buildInitialArguments({
        type: "object",
        allOf: [
          { properties: { path: { type: "string" } } },
          { properties: { recursive: { type: "boolean" } } },
        ],
        properties: {
          options: {
            type: "object",
            properties: {
              depth: { type: "integer", default: 2 },
              mode: { const: "safe" },
              patterns: { type: "array", items: { type: "string" } },
            },
          },
        },
      }),
    ).toEqual({
      path: "",
      recursive: false,
      options: { depth: 2, mode: "safe", patterns: [] },
    });
  });
});

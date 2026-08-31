import { describe, expect, it } from "vitest";
import { clampModelPage, MODEL_PAGE_SIZES, paginateModels } from "./modelPagination";

describe("模型广场分页", () => {
  it("只提供约定的每页数量", () => {
    expect(MODEL_PAGE_SIZES).toEqual([10, 20, 50, 100]);
  });

  it("切换页码后返回对应数据", () => {
    const items = Array.from({ length: 35 }, (_, index) => index + 1);
    expect(paginateModels(items, 2, 20)).toEqual(Array.from({ length: 15 }, (_, i) => i + 21));
  });

  it("数据减少后把页码收敛到最后一页", () => {
    expect(clampModelPage(5, 20, 35)).toBe(2);
  });
});

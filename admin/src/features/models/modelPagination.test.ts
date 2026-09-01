import { describe, expect, it } from "vitest";
import { clampModelPage, MODEL_PAGE_SIZES, paginateModels } from "./modelPagination";

describe("模型广场分页", () => {
  it("只提供约定的每页数量", () => {
    expect(MODEL_PAGE_SIZES).toEqual([12, 24, 48, 96]);
  });

  it("切换页码后返回对应数据", () => {
    const items = Array.from({ length: 35 }, (_, index) => index + 1);
    expect(paginateModels(items, 2, 12)).toEqual(Array.from({ length: 12 }, (_, i) => i + 13));
  });

  it("数据减少后把页码收敛到最后一页", () => {
    expect(clampModelPage(5, 12, 35)).toBe(3);
  });
});

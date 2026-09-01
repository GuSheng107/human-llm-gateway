export const MODEL_PAGE_SIZES = [12, 24, 48, 96] as const;
export type ModelPageSize = (typeof MODEL_PAGE_SIZES)[number];

export function modelPageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function clampModelPage(page: number, pageSize: number, total: number): number {
  return Math.min(Math.max(1, page), modelPageCount(total, pageSize));
}

export function paginateModels<T>(items: T[], page: number, pageSize: number): T[] {
  const safePage = clampModelPage(page, pageSize, items.length);
  const start = (safePage - 1) * pageSize;
  return items.slice(start, start + pageSize);
}

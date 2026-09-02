import { Icon } from "../../icons";

/** 全站统一的每页条数选项。 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

interface Props {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

/**
 * 分页条：始终展示（不分页也渲染），含每页条数选择（10/20/50/100）。
 * 一目了然地区分“共 N 条 / 页码 / 上一页下一页 / 每页条数”。
 */
export function Pagination({ page, pageSize, total, onChange, onPageSizeChange }: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex flex-wrap items-center gap-2 text-micro text-slate-400">
      <span>共 {total} 条</span>
      <div className="flex gap-1">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          className="grid h-6 w-6 place-items-center rounded border border-slate-200 bg-white text-slate-500 disabled:opacity-40"
          aria-label="上一页"
        >
          <Icon name="chevronLeft" className="h-3 w-3" />
        </button>
        <span className="inline-flex h-6 items-center px-2 text-slate-500">
          第 {page} / {totalPages} 页
        </span>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onChange(page + 1)}
          className="grid h-6 w-6 place-items-center rounded border border-slate-200 bg-white text-slate-500 disabled:opacity-40"
          aria-label="下一页"
        >
          <Icon name="chevronRight" className="h-3 w-3" />
        </button>
      </div>
      <label className="flex items-center gap-1.5">
        每页
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          className="h-6 rounded border border-slate-200 bg-white px-1 text-slate-600"
          aria-label="每页条数"
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
        条
      </label>
    </div>
  );
}

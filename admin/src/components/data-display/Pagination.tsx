import { Icon } from "../../icons";

interface Props {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pageSize, total, onChange }: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (totalPages <= 1) {
    return <span className="text-[10px] text-slate-400">共 {total} 条</span>;
  }
  return (
    <div className="flex items-center gap-2 text-[10px] text-slate-400">
      <span>共 {total} 条 · 第 {page}/{totalPages} 页</span>
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
    </div>
  );
}

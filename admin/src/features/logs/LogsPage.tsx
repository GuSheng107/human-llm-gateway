import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listLogs, type LogItem } from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { friendlyErrorMessage } from "../../utils/notify";
import { LogDetailModal } from "./LogDetailModal";

const DEFAULT_PAGE_SIZE = 20;
type Level = "debug" | "error" | "warning" | "info";

const LEVEL_META: Record<Level, { label: string; className: string }> = {
  debug: { label: "DEBUG", className: "border-slate-200 bg-slate-50 text-slate-500" },
  error: { label: "ERROR", className: "border-red-200 bg-red-50 text-red-700" },
  warning: { label: "WARNING", className: "border-amber-200 bg-amber-50 text-amber-700" },
  info: { label: "INFO", className: "border-slate-200 bg-slate-100 text-slate-600" },
};

const KIND_LABEL: Record<LogItem["kind"], string> = { audit: "审计", app: "应用" };

function formatTime(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function LevelBadge({ level }: { level: string }) {
  const meta = LEVEL_META[level as Level];
  return (
    <span
      className={`inline-flex rounded border px-2 py-0.5 text-[10px] font-semibold ${
        meta?.className ?? "border-slate-200 bg-slate-50 text-slate-500"
      }`}
    >
      {meta?.label ?? level.toUpperCase()}
    </span>
  );
}

function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded border border-slate-100 bg-slate-50/70 px-3 py-2">
      <dt className="text-[10px] text-slate-400">{label}</dt>
      <dd className="mt-1 break-all font-mono text-[11px] text-slate-700">{value || "-"}</dd>
    </div>
  );
}

export function LogsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTraceId = searchParams.get("trace_id") ?? "";
  const [items, setItems] = useState<LogItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailEntryId, setDetailEntryId] = useState<string | null>(null);
  const [detailTitle, setDetailTitle] = useState("");

  const [traceInput, setTraceInput] = useState(initialTraceId);
  const initialCategory = searchParams.get("category") ?? "";
  const initialEvent = searchParams.get("event") ?? "";
  const initialLevel = (searchParams.get("level") as Level | null) ?? "";
  const initialHours = searchParams.get("hours") ?? "";
  const initialKind = (searchParams.get("kind") as "audit" | "app" | null) ?? "";
  const [categoryInput, setCategoryInput] = useState(initialCategory);
  const [eventInput, setEventInput] = useState(initialEvent);
  const [levelInput, setLevelInput] = useState<Level | "">(initialLevel);
  const [hoursInput, setHoursInput] = useState(initialHours);
  const [kindInput, setKindInput] = useState<"audit" | "app" | "">(initialKind);
  const [filters, setFilters] = useState<{
    traceId: string;
    category: string;
    event: string;
    level: Level | "";
    hours: string;
    kind: "audit" | "app" | "";
  }>({
    traceId: initialTraceId,
    category: initialCategory,
    event: initialEvent,
    level: initialLevel,
    hours: initialHours,
    kind: initialKind,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listLogs({
        page,
        page_size: pageSize,
        trace_id: filters.traceId.trim() || undefined,
        category: filters.category.trim() || undefined,
        event: filters.event.trim() || undefined,
        level: filters.level || undefined,
        hours: filters.hours ? Number(filters.hours) : undefined,
        kind: filters.kind || undefined,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "加载日志失败"));
    } finally {
      setLoading(false);
    }
  }, [filters, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setFilters({
      traceId: traceInput,
      category: categoryInput,
      event: eventInput,
      level: levelInput,
      hours: hoursInput,
      kind: kindInput,
    });
    const params = new URLSearchParams(searchParams);
    const values: Record<string, string> = {
      trace_id: traceInput.trim(),
      category: categoryInput.trim(),
      event: eventInput.trim(),
      level: levelInput,
      hours: hoursInput.trim(),
      kind: kindInput,
    };
    for (const [key, value] of Object.entries(values)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    setSearchParams(params, { replace: true });
  };

  const filterByTrace = (traceId: string) => {
    setTraceInput(traceId);
    setPage(1);
    setFilters((current) => ({ ...current, traceId }));
    const params = new URLSearchParams(searchParams);
    params.set("trace_id", traceId);
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="日志查询"
        description="按 traceId 回溯请求链路；日志保留 7 天，详情中的上下文已按服务端规则脱敏。"
      />
      <Card>
        {error && <ErrorBanner message={error} className="m-4" />}
        <form onSubmit={submitSearch} className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
          <select
            value={kindInput}
            onChange={(event) => setKindInput(event.target.value as "audit" | "app" | "")}
            className="field-input sm:w-28"
            aria-label="日志类型"
          >
            <option value="">全部类型</option>
            <option value="app">应用</option>
            <option value="audit">审计</option>
          </select>
          <select
            value={levelInput}
            onChange={(event) => setLevelInput(event.target.value as Level | "")}
            className="field-input sm:w-32"
            aria-label="日志级别"
          >
            <option value="">全部级别</option>
            <option value="debug">DEBUG</option>
            <option value="error">ERROR</option>
            <option value="warning">WARNING</option>
            <option value="info">INFO</option>
          </select>
          <input
            value={categoryInput}
            onChange={(event) => setCategoryInput(event.target.value)}
            placeholder="分类（如 llm）"
            className="field-input min-w-[150px] flex-1 sm:max-w-[220px]"
          />
          <input
            value={eventInput}
            onChange={(event) => setEventInput(event.target.value)}
            placeholder="事件（模糊匹配）"
            className="field-input min-w-[180px] flex-1 sm:max-w-[280px]"
          />
          <input
            value={traceInput}
            onChange={(event) => setTraceInput(event.target.value)}
            placeholder="trace_id（精确匹配）"
            className="field-input min-w-[180px] flex-1 font-mono sm:max-w-[280px]"
          />
          <input
            type="number"
            min={1}
            max={720}
            value={hoursInput}
            onChange={(event) => setHoursInput(event.target.value)}
            placeholder="近 N 小时"
            className="field-input sm:w-28"
          />
          <Button type="submit" variant="ghost">
            <Icon name="search" className="h-3.5 w-3.5" />
            筛选
          </Button>
        </form>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="w-16 px-4 py-3 text-center font-medium">行号</th>
                <th className="px-4 py-3 font-medium">级别</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">来源 / 事件</th>
                <th className="px-4 py-3 font-medium">trace_id</th>
                <th className="px-4 py-3 font-medium">时间</th>
                <th className="px-4 py-3 font-medium">消息</th>
                <th className="w-24 px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item, index) => (
                <tr key={item.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-center text-slate-400">
                    {(page - 1) * pageSize + index + 1}
                  </td>
                  <td className="px-4 py-3"><LevelBadge level={item.level} /></td>
                  <td className="px-4 py-3">
                    <span className="rounded-full border border-primary-ghost bg-primary-faint px-2 py-0.5 text-[10px] text-primary">
                      {item.category || KIND_LABEL[item.kind]}
                    </span>
                  </td>
                  <td className="max-w-[240px] truncate px-4 py-3 font-mono text-slate-600">
                    <span className="mr-1.5 text-slate-400">{KIND_LABEL[item.kind]}</span>
                    {item.event || "-"}
                  </td>
                  <td className="max-w-[240px] break-all px-4 py-3 font-mono text-[11px]">
                    {item.request_id ? (
                      <button
                        type="button"
                        title="按此 trace_id 过滤"
                        onClick={() => filterByTrace(item.request_id ?? "")}
                        className="text-primary hover:underline"
                      >
                        {item.request_id}
                      </button>
                    ) : <span className="text-slate-300">-</span>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                    {formatTime(item.created_at)}
                  </td>
                  <td className="max-w-[320px] truncate px-4 py-3 text-slate-500">
                    <span className="flex items-center gap-1.5">
                      {item.duration_ms != null && (
                        <span
                          className="shrink-0 rounded bg-slate-100 px-1 py-0.5 font-mono text-[9px] text-slate-500"
                          title="上游耗时"
                        >
                          {item.duration_ms >= 1000
                            ? `${(item.duration_ms / 1000).toFixed(1)}s`
                            : `${item.duration_ms}ms`}
                        </span>
                      )}
                      {item.status_code != null && (
                        <span
                          className={`shrink-0 rounded px-1 py-0.5 font-mono text-[9px] ${
                            item.status_code >= 400
                              ? "bg-red-50 text-red-600"
                              : "bg-slate-100 text-slate-500"
                          }`}
                          title="HTTP 状态码"
                        >
                          {item.status_code}
                        </span>
                      )}
                      <span className="min-w-0 truncate">{item.message || "-"}</span>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => {
                        setDetailEntryId(item.id);
                        setDetailTitle(
                          `${KIND_LABEL[item.kind]} · ${item.event || item.message.slice(0, 30)}`,
                        );
                      }}
                      className="text-primary hover:underline"
                    >
                      查看详情
                      {item.has_detail && (
                        <span
                          className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-primary align-middle"
                          title="含详情信封"
                        />
                      )}
                    </button>
                  </td>
                </tr>
              ))}
              {loading && (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-400">加载中…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-400">暂无日志</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onChange={setPage}
            onPageSizeChange={(size) => {
              setPage(1);
              setPageSize(size);
            }}
          />
        </div>
      </Card>

      {detailEntryId && (
        <LogDetailModal
          entryId={detailEntryId}
          title={detailTitle}
          onClose={() => {
            setDetailEntryId(null);
            setDetailTitle("");
          }}
          onFilterByTrace={(traceId) => {
            setDetailEntryId(null);
            filterByTrace(traceId);
          }}
        />
      )}
    </div>
  );
}

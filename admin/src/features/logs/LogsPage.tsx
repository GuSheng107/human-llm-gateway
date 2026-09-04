import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listLogs, type LogItem } from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { friendlyErrorMessage } from "../../utils/notify";

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
  const [detail, setDetail] = useState<LogItem | null>(null);

  const [traceInput, setTraceInput] = useState(initialTraceId);
  const initialCategory = searchParams.get("category") ?? "";
  const initialEvent = searchParams.get("event") ?? "";
  const initialLevel = (searchParams.get("level") as Level | null) ?? "";
  const initialHours = searchParams.get("hours") ?? "";
  const [categoryInput, setCategoryInput] = useState(initialCategory);
  const [eventInput, setEventInput] = useState(initialEvent);
  const [levelInput, setLevelInput] = useState<Level | "">(initialLevel);
  const [hoursInput, setHoursInput] = useState(initialHours);
  const [filters, setFilters] = useState<{
    traceId: string;
    category: string;
    event: string;
    level: Level | "";
    hours: string;
  }>({
    traceId: initialTraceId,
    category: initialCategory,
    event: initialEvent,
    level: initialLevel,
    hours: initialHours,
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
    });
    const params = new URLSearchParams(searchParams);
    const values: Record<string, string> = {
      trace_id: traceInput.trim(),
      category: categoryInput.trim(),
      event: eventInput.trim(),
      level: levelInput,
      hours: hoursInput.trim(),
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

  const contextText = detail?.context ? JSON.stringify(detail.context, null, 2) : "暂无上下文";

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
                    {item.message || "-"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => setDetail(item)}
                      className="text-primary hover:underline"
                    >
                      查看详情
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

      {detail && (
        <Modal
          title="日志详情"
          description={`${KIND_LABEL[detail.kind]} · ${detail.event || "未命名事件"}`}
          onClose={() => setDetail(null)}
          width="max-w-4xl"
        >
          <div className="max-h-[75vh] space-y-5 overflow-y-auto p-6 text-xs">
            <dl className="grid gap-2 sm:grid-cols-2">
              <DetailField label="级别" value={<LevelBadge level={detail.level} />} />
              <DetailField label="类型" value={detail.category || "-"} />
              <DetailField label="来源 / 事件" value={detail.event} />
              <DetailField label="日志 ID" value={detail.id} />
              <DetailField label="Trace ID" value={detail.request_id} />
              <DetailField label="创建时间" value={formatTime(detail.created_at)} />
              <DetailField label="用户" value={detail.username ?? detail.user_id} />
              <DetailField label="任务 ID" value={detail.task_id} />
              <DetailField label="API Key ID" value={detail.api_key_id} />
              <DetailField label="连接 ID" value={detail.connection_id} />
            </dl>
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">消息</h3>
              <p className="whitespace-pre-wrap rounded border border-slate-100 bg-slate-50 p-3 text-slate-600">
                {detail.message || "-"}
              </p>
            </section>
            <section>
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium text-slate-700">Context JSON</h3>
                {detail.context && (
                  <Button type="button" variant="ghost" onClick={() => void copyText(contextText, "Context JSON")}>
                    <Icon name="copy" className="h-3.5 w-3.5" />
                    复制
                  </Button>
                )}
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-slate-100 bg-slate-900 p-4 font-mono text-[11px] leading-5 text-slate-100">
                {contextText}
              </pre>
            </section>
          </div>
        </Modal>
      )}
    </div>
  );
}

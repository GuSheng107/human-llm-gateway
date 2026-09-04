import { type FormEvent, useCallback, useEffect, useState } from "react";
import { listLogs, type LogItem } from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { friendlyErrorMessage } from "../../utils/notify";

const DEFAULT_PAGE_SIZE = 20;
const LOG_RETENTION_DAYS = 7;

function formatTime(value: string): string {
  return value ? new Date(value).toLocaleString() : "-";
}

const KIND_LABEL: Record<LogItem["kind"], string> = {
  audit: "审计",
  app: "应用",
};

export function LogsPage() {
  const [items, setItems] = useState<LogItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [traceId, setTraceId] = useState("");
  const [eventInput, setEventInput] = useState("");
  const [hours, setHours] = useState("");
  const [filters, setFilters] = useState<{ traceId: string; event: string; hours: string }>({
    traceId: "",
    event: "",
    hours: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listLogs({
        page,
        page_size: pageSize,
        trace_id: filters.traceId.trim() || undefined,
        event: filters.event.trim() || undefined,
        hours: filters.hours ? Number(filters.hours) : undefined,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "加载失败"));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setFilters({ traceId, event: eventInput, hours });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="日志查询"
        description={`按 traceId 串联审计与应用日志；普通用户仅可见与自己相关的行。日志保留 ${LOG_RETENTION_DAYS} 天。`}
      />

      <Card>
        {error && <ErrorBanner message={error} className="m-4" />}

        <form
          onSubmit={submitSearch}
          className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3"
        >
          <input
            value={traceId}
            onChange={(event) => setTraceId(event.target.value)}
            placeholder="traceId（request_id）"
            className="field-input flex-1 min-w-[200px] font-mono sm:max-w-[280px]"
          />
          <input
            value={eventInput}
            onChange={(event) => setEventInput(event.target.value)}
            placeholder="事件 / 动作"
            className="field-input min-w-0 flex-1 sm:max-w-[220px]"
          />
          <input
            type="number"
            min={1}
            max={720}
            value={hours}
            onChange={(event) => setHours(event.target.value)}
            placeholder="近 N 小时"
            className="field-input sm:w-28"
          />
          <Button type="submit" variant="ghost">
            <Icon name="search" className="h-3.5 w-3.5" />
            筛选
          </Button>
        </form>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">时间</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">级别</th>
                <th className="px-4 py-3 font-medium">用户</th>
                <th className="px-4 py-3 font-medium">事件 / 动作</th>
                <th className="px-4 py-3 font-medium">消息</th>
                <th className="px-4 py-3 font-medium">traceId</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50/60">
                  <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                    {formatTime(item.created_at)}
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {KIND_LABEL[item.kind] ?? item.kind}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.level} fallback={item.level} />
                  </td>
                  <td className="px-4 py-3 text-slate-600">{item.username ?? "-"}</td>
                  <td className="max-w-[200px] truncate px-4 py-3 font-mono text-slate-600">
                    {item.event || "-"}
                  </td>
                  <td className="max-w-[320px] truncate px-4 py-3 text-slate-500">
                    {item.message}
                  </td>
                  <td className="max-w-[220px] break-all px-4 py-3 font-mono text-[11px] text-slate-500">
                    {item.request_id ?? "-"}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                    暂无日志
                  </td>
                </tr>
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
    </div>
  );
}

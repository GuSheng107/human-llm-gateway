import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listTasks } from "../../api/tasks";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import type { TaskItem, TaskState } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import { DeadlineBadge } from "./DeadlineBadge";
import { TaskDetailDrawer } from "./TaskDetailDrawer";
import {
  DELIVERY_MODE_LABELS,
  PROTOCOL_LABELS,
  REPLY_STRATEGY_LABELS,
  STATE_FILTER_OPTIONS,
  formatDateTime,
  isTerminalTaskState,
} from "./labels";

const DEFAULT_PAGE_SIZE = 20;
// 自动刷新间隔（毫秒）：页面隐藏时暂停。
const REFRESH_INTERVAL_MS = 5000;

type Bucket = "all" | "in_progress" | "finished" | "failed";

const BUCKETS: { value: Bucket; label: string }[] = [
  { value: "in_progress", label: "进行中" },
  { value: "finished", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "all", label: "全部" },
];

const DEFAULT_BUCKET: Bucket = "in_progress";

export function TasksPage() {
  const { user: currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";
  const [searchParams, setSearchParams] = useSearchParams();
  const bucketParam = searchParams.get("bucket") as Bucket | null;
  const bucket: Bucket =
    bucketParam && BUCKETS.some((option) => option.value === bucketParam)
      ? bucketParam
      : DEFAULT_BUCKET;
  const deepLinkTask = searchParams.get("task");
  const [items, setItems] = useState<TaskItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<TaskState | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailId, setDetailId] = useState<string | null>(deepLinkTask);

  const updateBucket = (next: Bucket) => {
    setPage(1);
    const params = new URLSearchParams(searchParams);
    if (next === DEFAULT_BUCKET) params.delete("bucket");
    else params.set("bucket", next);
    setSearchParams(params, { replace: true });
  };

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const result = await listTasks({
          page,
          search,
          state: stateFilter || undefined,
          bucket: bucket === "all" ? undefined : bucket,
          pageSize,
        });
        setItems(result.items);
        setTotal(result.total);
        setError("");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "加载失败");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [page, pageSize, search, stateFilter, bucket],
  );

  const changePageSize = (value: number) => {
    setPage(1);
    setPageSize(value);
  };

  useEffect(() => void load(), [load]);

  // 自动轮询：任务状态随推进刷新，避免页面停留在过期状态。
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void load(true);
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const closeDetail = () => {
    setDetailId(null);
    if (deepLinkTask) {
      const params = new URLSearchParams(searchParams);
      params.delete("task");
      setSearchParams(params, { replace: true });
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="任务记录"
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
          <div className="flex rounded-md border border-slate-200 p-0.5">
            {BUCKETS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => updateBucket(option.value)}
                className={`rounded px-3 py-1 text-xs transition ${
                  bucket === option.value
                    ? "bg-primary text-white"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <form onSubmit={submitSearch} className="flex min-w-0 flex-1 gap-2">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              className="field-input min-w-0 flex-1 sm:max-w-sm"
              placeholder="搜索任务编号或模型"
            />
            <select
              value={stateFilter}
              onChange={(event) => {
                setStateFilter(event.target.value as TaskState | "");
                setPage(1);
              }}
              className="field-input sm:w-44"
            >
              <option value="">全部状态</option>
              {STATE_FILTER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <Button variant="ghost" type="submit">
              <Icon name="search" className="h-3.5 w-3.5" />
              搜索
            </Button>
          </form>
        </div>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[960px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">任务编号</th>
                <th className="px-4 py-3 font-medium">模型</th>
                <th className="px-4 py-3 font-medium">协议</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">策略</th>
                <th className="px-4 py-3 font-medium">投递</th>
                <th className="px-4 py-3 font-medium">创建时间</th>
                <th className="px-4 py-3 font-medium">人工截止</th>
                {isAdmin && <th className="px-4 py-3 font-medium">归属</th>}
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((task) => (
                <tr key={task.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-mono text-slate-700">
                    <span className="inline-flex items-center gap-1.5">
                      {task.has_tools && (
                        <Icon name="code" className="h-3.5 w-3.5 text-primary" />
                      )}
                      #{task.public_id}
                      <button
                        type="button"
                        aria-label={`复制任务编号 ${task.public_id}`}
                        title="复制任务编号"
                        onClick={() => void copyText(task.public_id, "任务编号")}
                        className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-primary"
                      >
                        <Icon name="copy" className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{task.fake_model_name}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {PROTOCOL_LABELS[task.protocol] ?? task.protocol}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={task.state} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {REPLY_STRATEGY_LABELS[task.reply_strategy] ?? task.reply_strategy}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {DELIVERY_MODE_LABELS[task.delivery_mode] ?? task.delivery_mode}
                    {task.stream_requested && (
                      <span className="ml-1 text-slate-400">流式</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDateTime(task.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    {isTerminalTaskState(task.state) ? (
                      <span className="text-slate-300">-</span>
                    ) : (
                      <DeadlineBadge deadline={task.human_deadline_at} />
                    )}
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-slate-500">
                      {task.owner_username ?? "-"}
                    </td>
                  )}
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => setDetailId(task.id)} className="text-primary">
                      详情
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td
                    colSpan={isAdmin ? 10 : 9}
                    className="px-4 py-12 text-center text-slate-400"
                  >
                    暂无任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage} onPageSizeChange={changePageSize} />
        </div>
      </Card>

      {detailId && (
        <TaskDetailDrawer
          taskId={detailId}
          onClose={closeDetail}
          onChanged={() => void load()}
        />
      )}
    </div>
  );
}

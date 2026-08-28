import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getTask, listTasks, replyTask } from "../../api/tasks";
import { Drawer } from "../../components/feedback/Drawer";
import { notify } from "../../components/feedback/Toast";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Icon } from "../../icons";
import type { TaskDetail, TaskSummary } from "../../types/tasks";

const STATUS_OPTIONS = ["", "human_waiting", "llm_streaming", "pseudo_streaming", "completed", "timeout", "failed"];

const SOURCE_META: Record<string, { label: string; className: string }> = {
  im: { label: "人工 · IM", className: "bg-emerald-50 text-emerald-700" },
  web: { label: "人工 · Web", className: "bg-blue-50 text-blue-700" },
  llm: { label: "LLM", className: "bg-purple-50 text-purple-700" },
};

const KIND_LABELS: Record<string, string> = {
  reasoning: "思考",
  tool_call: "工具调用",
  final: "最终回复",
};

const DSL_TEMPLATE = "/think\n在此填写思考过程\n/reply\n在此填写最终回复\n/done";

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const openTaskId = searchParams.get("task");

  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [replyText, setReplyText] = useState(DSL_TEMPLATE);
  const [replying, setReplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listTasks({ page, page_size: 20, status: statusFilter || undefined });
      setTasks(result.items);
      setTotal(result.total);
    } catch (error) {
      notify(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!openTaskId) {
      setDetail(null);
      return;
    }
    getTask(openTaskId)
      .then(setDetail)
      .catch((error) => {
        notify(error instanceof Error ? error.message : "任务详情加载失败");
        setSearchParams({}, { replace: true });
      });
  }, [openTaskId, setSearchParams]);

  const openTask = (taskId: string) => {
    setSearchParams({ task: taskId });
  };

  const closeTask = () => {
    setSearchParams({});
    setDetail(null);
  };

  const submitReply = async () => {
    if (!detail) return;
    setReplying(true);
    try {
      await replyTask(detail.id, replyText);
      notify("回复已提交");
      const refreshed = await getTask(detail.id);
      setDetail(refreshed);
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "回复失败");
    } finally {
      setReplying(false);
    }
  };

  const canReply = detail && (detail.status === "human_waiting" || detail.status === "tool_pending");

  return (
    <div className="mx-auto max-w-[1300px] space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">任务与网页回复</h1>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          查看每个请求任务的摘要、来源（人工 IM / 人工 Web / LLM / fallback）与事件时间线；等待中的任务可在此用 DSL 直接回复。
        </p>
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-[#409eff]">
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>{status === "" ? "全部状态" : status}</option>
            ))}
          </select>
          <Pagination page={page} pageSize={20} total={total} onChange={setPage} />
        </div>
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <th className="px-5 py-3">任务 ID</th>
              <th className="px-4 py-3">协议 / 模型</th>
              <th className="px-4 py-3">模式</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3">创建时间</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
            ) : tasks.length === 0 ? (
              <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">暂无任务</td></tr>
            ) : tasks.map((task) => (
              <tr key={task.id} className="cursor-pointer text-xs transition hover:bg-[#f7fbff]" onClick={() => openTask(task.id)}>
                <td className="px-5 py-3.5 font-mono text-slate-700">{task.id.slice(0, 13)}…</td>
                <td className="px-4 py-3.5 text-slate-600">{task.protocol} · {task.model_name ?? task.model}</td>
                <td className="px-4 py-3.5">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${
                    task.route_mode === "human" ? "bg-emerald-50 text-emerald-600"
                    : task.route_mode === "llm" ? "bg-blue-50 text-blue-600"
                    : "bg-purple-50 text-purple-600"
                  }`}>
                    {task.route_mode === "human" ? "人工" : task.route_mode === "llm" ? "LLM" : "人工→LLM"}
                  </span>
                </td>
                <td className="px-4 py-3.5"><StatusBadge status={task.status} /></td>
                <td className="px-4 py-3.5 text-slate-500">{new Date(task.created_at).toLocaleString("zh-CN")}</td>
                <td className="px-5 py-3.5 text-right">
                  {task.status === "human_waiting" && (
                    <span className="rounded bg-amber-50 px-2 py-0.5 text-[10px] text-amber-600">待回复</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {detail && (
        <Drawer title={`任务 ${detail.id}`} description={`${detail.protocol} · 模型 ${detail.model_name ?? detail.model}`} onClose={closeTask} width="max-w-3xl">
          <div className="space-y-6 px-6 py-5">
            <section>
              <h3 className="mb-2 text-xs font-semibold text-slate-600">请求摘要</h3>
              <dl className="divide-y divide-slate-100 rounded-md border border-slate-200 text-xs">
                <div className="flex items-center justify-between px-4 py-2.5">
                  <dt className="text-slate-400">状态</dt>
                  <dd><StatusBadge status={detail.status} /></dd>
                </div>
                <div className="flex items-center justify-between px-4 py-2.5">
                  <dt className="text-slate-400">路由模式</dt>
                  <dd className="text-slate-700">
                    {detail.route_mode === "human" ? "人工" : detail.route_mode === "llm" ? "LLM" : "人工→LLM fallback"}
                  </dd>
                </div>
                <div className="flex items-center justify-between px-4 py-2.5">
                  <dt className="text-slate-400">创建时间</dt>
                  <dd className="text-slate-700">{new Date(detail.created_at).toLocaleString("zh-CN")}</dd>
                </div>
                {detail.error && (
                  <div className="px-4 py-2.5">
                    <dt className="text-slate-400">错误</dt>
                    <dd className="mt-1 rounded bg-red-50 px-2 py-1.5 text-[11px] text-red-500">{detail.error}</dd>
                  </div>
                )}
              </dl>
            </section>

            {canReply && (
              <section>
                <h3 className="mb-2 text-xs font-semibold text-slate-600">网页回复（DSL）</h3>
                <textarea
                  value={replyText}
                  onChange={(event) => setReplyText(event.target.value)}
                  className="field-input min-h-40 resize-y font-mono text-[12px]"
                  spellCheck={false}
                />
                <div className="mt-2 flex items-center justify-between">
                  <p className="text-[10px] text-slate-400">DSL：/think 思考 · /tool 调用 · /reply 回复 · /done 结束</p>
                  <button
                    type="button"
                    disabled={replying}
                    onClick={() => void submitReply()}
                    className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50"
                  >
                    {replying ? "提交中…" : "提交回复"}
                  </button>
                </div>
              </section>
            )}

            <section>
              <h3 className="mb-2 text-xs font-semibold text-slate-600">事件时间线</h3>
              {detail.events.length === 0 ? (
                <p className="rounded-md border border-dashed border-slate-200 px-4 py-6 text-center text-xs text-slate-400">
                  还没有事件；等待人工或 LLM 回复后将按顺序记录。
                </p>
              ) : (
                <ol className="space-y-3">
                  {detail.events.map((event) => (
                    <li key={event.sequence} className="rounded-md border border-slate-200 bg-white">
                      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[10px] text-slate-300">#{event.sequence}</span>
                          <span className="text-[11px] font-medium text-slate-600">{KIND_LABELS[event.kind] ?? event.kind}</span>
                          {event.tool_name && (
                            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">{event.tool_name}</code>
                          )}
                        </div>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] ${SOURCE_META[event.source]?.className ?? "bg-slate-100 text-slate-500"}`}>
                          {SOURCE_META[event.source]?.label ?? event.source}
                        </span>
                      </div>
                      {event.content && (
                        <pre className="overflow-x-auto whitespace-pre-wrap px-4 py-3 text-[11px] leading-5 text-slate-700">{event.content}</pre>
                      )}
                      {event.tool_args && (
                        <pre className="overflow-x-auto border-t border-slate-100 px-4 py-2 font-mono text-[10px] text-slate-500">{event.tool_args}</pre>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        </Drawer>
      )}
    </div>
  );
}

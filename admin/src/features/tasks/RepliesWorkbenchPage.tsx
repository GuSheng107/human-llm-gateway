import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listTasks } from "../../api/tasks";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { TaskItem } from "../../types/gateway";

const PAGE_SIZE = 20;

// 工作台状态徽章：进行中的细分状态统一归为“待回复/处理中”。
const WORKBENCH_BADGE: Record<string, string> = {
  received: "connecting",
  waiting_human: "pending_restart",
  forwarding_llm: "connecting",
  response_ready: "active",
  responding: "connecting",
};

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    received: "已接收",
    waiting_human: "等待回复",
    forwarding_llm: "LLM 处理中",
    response_ready: "待确认输出",
    responding: "输出中",
  };
  return labels[state] ?? state;
}

function deadlineText(task: TaskItem): string {
  if (!task.human_deadline_at) return "-";
  const remainingMs = new Date(task.human_deadline_at).getTime() - Date.now();
  if (remainingMs <= 0) return "已超时";
  const minutes = Math.ceil(remainingMs / 60000);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分钟`;
}

export function RepliesWorkbenchPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<TaskItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listTasks({ page, bucket: "in_progress" });
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="回复工作台"
        dismissId="replies"
        actions={
          <Button variant="ghost" onClick={() => void load()}>
            <Icon name="refresh" className="h-4 w-4" />
            刷新
          </Button>
        }
      />

      {error && <ErrorBanner message={error} />}

      <Card>
        {loading ? (
          <div className="px-4 py-12 text-center text-xs text-slate-400">加载中…</div>
        ) : items.length === 0 ? (
          <div className="px-4 py-12 text-center text-xs text-slate-400">
            当前没有等待回复的任务
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[860px] w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">任务</th>
                  <th className="px-4 py-3 font-medium">模型</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium">回复剩余时间</th>
                  <th className="px-4 py-3 font-medium">创建时间</th>
                  <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((task) => (
                  <tr key={task.id} className="group hover:bg-slate-50/60">
                    <td className="px-4 py-3">
                      <span className="font-mono font-medium text-slate-700">
                        #{task.public_id}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500">{task.fake_model_name}</td>
                    <td className="px-4 py-3">
                      <StatusBadge
                        status={WORKBENCH_BADGE[task.state] ?? "inactive"}
                        fallback={stateLabel(task.state)}
                      />
                    </td>
                    <td className="px-4 py-3 text-slate-500">{deadlineText(task)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                      {new Date(task.created_at).toLocaleString()}
                    </td>
                    <td className="sticky right-0 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                      <button
                        type="button"
                        onClick={() => navigate(`/tasks/${task.id}/reply`)}
                        className="text-primary"
                      >
                        进入回复
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>
    </div>
  );
}

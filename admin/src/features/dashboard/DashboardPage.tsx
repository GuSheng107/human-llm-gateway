import { useEffect, useState } from "react";
import { getDashboard, type DashboardData } from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { useAuth } from "../auth/AuthContext";

const PROTOCOL_LABELS: Record<string, string> = {
  openai_chat: "OpenAI Chat",
  openai_responses: "Responses",
  anthropic_messages: "Anthropic",
};

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <Card>
      <div className="px-4 py-4">
        <span className="block text-xs text-slate-400">{label}</span>
        <span className="mt-1 block text-2xl font-semibold text-slate-800">{value}</span>
      </div>
    </Card>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getDashboard()
      .then(setData)
      .catch((caught) => setError(caught instanceof Error ? caught.message : "加载失败"));
  }, []);

  const stats = data?.stats;
  const userCards = stats && !isAdmin && (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard label="进行中的任务" value={stats.my_active_tasks} />
      <StatCard label="累计任务" value={stats.my_total_tasks} />
      <StatCard label="API Key" value={stats.my_api_keys} />
      <StatCard label="LLM 配置" value={stats.my_llm_configs} />
    </div>
  );
  const adminCards = stats && isAdmin && (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard label="用户（启用）" value={`${stats.active_users}/${stats.total_users}`} />
      <StatCard label="全局活动任务" value={stats.global_active_tasks} />
      <StatCard label="累计任务" value={stats.total_tasks} />
      <StatCard label="API Key / 连接" value={`${stats.total_api_keys}/${stats.total_connections}`} />
    </div>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={isAdmin ? "监管控制台" : "控制台"}
        description={isAdmin ? "全局运行概览与治理数据" : "账号与任务概览"}
      />

      {error && <ErrorBanner message={error} />}
      {userCards}
      {adminCards}

      <Card>
        <div className="border-b border-slate-100 px-5 py-3 text-sm font-medium text-slate-700">
          最近任务
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">任务编号</th>
                <th className="px-4 py-3 font-medium">模型</th>
                <th className="px-4 py-3 font-medium">协议</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {(data?.recent_tasks ?? []).map((task) => (
                <tr key={task.id} className="hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-mono text-slate-700">#{task.public_id}</td>
                  <td className="px-4 py-3 font-mono text-slate-500">{task.model}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {PROTOCOL_LABELS[task.protocol] ?? task.protocol}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={task.state} />
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {task.created_at ? new Date(task.created_at).toLocaleString() : "-"}
                  </td>
                </tr>
              ))}
              {data && data.recent_tasks.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-slate-400">
                    暂无任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
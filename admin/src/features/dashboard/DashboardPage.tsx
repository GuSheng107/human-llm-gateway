import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../auth/AuthContext";
import type { IMConnection } from "../../types/connections";
import type { ApiKey, ModelRoute, Provider } from "../../types/llm";
import type { Paged, TaskSummary } from "../../types/tasks";

interface Stats {
  connections: IMConnection[];
  providers: number;
  routes: number;
  keys: number;
  recentTasks: TaskSummary[];
}

export function DashboardPage() {
  const { user } = useAuth();
  const location = useLocation();
  const isAdminView = location.pathname.startsWith("/admin");
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [connections, providers, routes, keys, tasks] = await Promise.all([
          api<IMConnection[]>("/api/im-connections"),
          api<Paged<Provider>>("/api/providers?page=1&page_size=1"),
          api<Paged<ModelRoute>>("/api/model-routes?page=1&page_size=1"),
          api<Paged<ApiKey>>("/api/api-keys?page=1&page_size=1"),
          api<Paged<TaskSummary>>("/api/tasks?page=1&page_size=5"),
        ]);
        if (!cancelled) {
          setStats({
            connections,
            providers: providers.total,
            routes: routes.total,
            keys: keys.total,
            recentTasks: tasks.items,
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const online = stats?.connections.filter((c) => c.status === "online").length ?? 0;
  const waiting = stats?.recentTasks.filter((t) => t.status === "human_waiting").length ?? 0;

  const cards = [
    { label: isAdminView ? "全部连接" : "我的连接", value: stats?.connections.length ?? 0, to: isAdminView ? "/admin/connections" : "/connections", tone: "text-slate-800" },
    { label: "在线连接", value: online, to: isAdminView ? "/admin/connections" : "/connections", tone: "text-emerald-600" },
    { label: "LLM 供应商", value: stats?.providers ?? 0, to: isAdminView ? "/admin/providers" : "/llm/providers", tone: "text-slate-800" },
    { label: "模型路由", value: stats?.routes ?? 0, to: isAdminView ? "/admin/routes" : "/llm/routes", tone: "text-slate-800" },
    { label: "API Key", value: stats?.keys ?? 0, to: isAdminView ? "/admin/api-keys" : "/llm/api-keys", tone: "text-slate-800" },
    { label: "等待人工任务", value: waiting, to: "/admin/tasks", tone: "text-amber-600" },
  ];

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">
          {isAdminView ? `监管控制台` : `你好，${user?.display_name || user?.username}`}
        </h1>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          {isAdminView
            ? "查看全局运行指标，深入各监管页面处理异常。"
            : "按「连接 IM → LLM 配置 → 签发 Key」三步接入，外部请求将转发到你的 IM 或等待网页回复。"}
        </p>
      </section>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {cards.map((card) => (
          <Link
            key={card.label}
            to={card.to}
            className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm transition hover:border-[#a0cfff]"
          >
            <div className="text-[11px] text-slate-400">{card.label}</div>
            <div className={`mt-2 font-mono text-2xl font-semibold ${card.tone}`}>
              {loading ? "…" : card.value}
            </div>
          </Link>
        ))}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4 text-sm font-medium text-slate-700">最近任务</div>
        <div className="divide-y divide-slate-100">
          {loading ? (
            <div className="px-5 py-10 text-center text-xs text-slate-400">加载中…</div>
          ) : stats?.recentTasks.length === 0 ? (
            <div className="px-5 py-10 text-center text-xs text-slate-400">暂无任务</div>
          ) : (
            stats?.recentTasks.map((task) => (
              <div key={task.id} className="flex items-center justify-between px-5 py-3 text-xs">
                <div className="min-w-0">
                  <div className="truncate font-mono text-slate-600">{task.id}</div>
                  <div className="mt-1 text-[10px] text-slate-400">
                    {task.protocol} · {task.model_name ?? task.model} · {new Date(task.created_at).toLocaleString("zh-CN")}
                  </div>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${
                  task.status === "completed" ? "bg-emerald-50 text-emerald-600"
                  : task.status === "human_waiting" ? "bg-amber-50 text-amber-600"
                  : task.status === "failed" || task.status === "timeout" ? "bg-red-50 text-red-600"
                  : "bg-blue-50 text-blue-600"
                }`}>
                  {task.status}
                </span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

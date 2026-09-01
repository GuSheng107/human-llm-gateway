import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getDashboard,
  type DashboardData,
  type DashboardRecentTask,
} from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { useAuth } from "../auth/AuthContext";
import { formatDeadline } from "../tasks/labels";

const PROTOCOL_LABELS: Record<string, string> = {
  openai_chat: "OpenAI Chat",
  openai_responses: "Responses",
  anthropic_messages: "Anthropic",
};

const PROTOCOL_COLORS: Record<string, string> = {
  openai_chat: "#2563eb",
  openai_responses: "#0f766e",
  anthropic_messages: "#d97706",
};

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const points = values
    .map(
      (value, index) =>
        `${(index / Math.max(1, values.length - 1)) * 100},${26 - (value / max) * 22}`,
    )
    .join(" ");
  return (
    <svg viewBox="0 0 100 28" className="h-8 w-16" aria-hidden="true">
      <polygon points={`0,28 ${points} 100,28`} fill="currentColor" opacity=".08" />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function StatCard({
  label,
  value,
  values,
  icon,
  tone,
}: {
  label: string;
  value: number | string;
  values: number[];
  icon: string;
  tone: string;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="relative flex min-h-28 items-center gap-3 px-5 py-4">
        <span className={`absolute inset-y-0 left-0 w-1 ${tone}`} />
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-blue-50 text-primary">
          <Icon name={icon} className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className="leading-5">{label}</span>
            <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
              实时
            </span>
          </div>
          <span className="mt-1.5 block text-2xl font-semibold tracking-tight text-slate-800">
            {value}
          </span>
        </div>
        <div className="hidden text-primary/70 sm:block">
          <Sparkline values={values} />
        </div>
      </div>
    </Card>
  );
}

function TaskLink({ task }: { task: DashboardRecentTask }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate(`/tasks?task=${task.id}`)}
      className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-slate-50"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-xs font-medium text-slate-700">
          #{task.public_id} · {task.model}
        </span>
        <span className="mt-0.5 block text-[11px] text-slate-400">
          {PROTOCOL_LABELS[task.protocol] ?? task.protocol}
          {task.human_deadline_at ? ` · ${formatDeadline(task.human_deadline_at)}` : ""}
        </span>
      </span>
      <StatusBadge status={task.state} />
    </button>
  );
}

function TaskTimeline({ data }: { data: DashboardData }) {
  const max = Math.max(1, ...data.daily_tasks.map((item) => item.count));
  return (
    <Card>
      <div className="border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-medium text-slate-700">最近 7 天任务量</h2>
      </div>
      <div className="relative flex h-56 items-end gap-3 overflow-hidden px-5 pb-5 pt-8 before:absolute before:inset-x-5 before:top-1/4 before:border-t before:border-dashed before:border-slate-100 after:absolute after:inset-x-5 after:top-1/2 after:border-t after:border-dashed after:border-slate-100">
        {data.daily_tasks.map((item) => (
          <div key={item.date} className="relative z-1 flex h-full min-w-0 flex-1 flex-col justify-end text-center">
            <span className="mb-1 text-[11px] font-medium text-slate-500">{item.count}</span>
            <div
              className="min-h-1 rounded-t bg-gradient-to-t from-blue-600 to-cyan-400 transition-all"
              style={{ height: `${Math.max(4, (item.count / max) * 120)}px` }}
              title={`${item.date}: ${item.count}`}
            />
            <span className="mt-2 text-[10px] text-slate-400">{item.date.slice(5)}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ProtocolDistribution({ data }: { data: DashboardData }) {
  const total = data.protocol_counts.reduce((sum, item) => sum + item.count, 0);
  let offset = 0;
  const segments = data.protocol_counts.map((item) => {
    const length = total ? (item.count / total) * 100 : 0;
    const segment = { ...item, length, offset };
    offset += length;
    return segment;
  });
  return (
    <Card>
      <div className="border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-medium text-slate-700">协议分布</h2>
      </div>
      <div className="flex min-h-56 flex-col items-center gap-5 p-5 sm:flex-row">
        <div className="relative h-32 w-32 shrink-0">
          <svg viewBox="0 0 42 42" className="h-full w-full -rotate-90" aria-label="协议分布图">
            <circle cx="21" cy="21" r="15.915" fill="none" stroke="#e2e8f0" strokeWidth="6" />
            {segments.map((item) => (
              <circle
                key={item.protocol}
                cx="21"
                cy="21"
                r="15.915"
                fill="none"
                stroke={PROTOCOL_COLORS[item.protocol] ?? "#64748b"}
                strokeWidth="6"
                strokeDasharray={`${item.length} ${100 - item.length}`}
                strokeDashoffset={-item.offset}
              />
            ))}
          </svg>
          <span className="absolute inset-0 grid place-items-center text-xl font-semibold text-slate-700">
            {total}
          </span>
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          {segments.map((item) => (
            <div key={item.protocol} className="flex items-center gap-2 text-xs">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: PROTOCOL_COLORS[item.protocol] ?? "#64748b" }}
              />
              <span className="min-w-0 flex-1 truncate text-slate-500">
                {PROTOCOL_LABELS[item.protocol] ?? item.protocol}
              </span>
              <span className="font-medium text-slate-700">{item.count}</span>
            </div>
          ))}
          {segments.length === 0 && <p className="text-xs text-slate-400">暂无任务</p>}
        </div>
      </div>
    </Card>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const isAdmin = user?.role === "admin";
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setData(await getDashboard());
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const values = useMemo(() => data?.daily_tasks.map((item) => item.count) ?? [], [data]);
  const stats = data?.stats;
  const cards: Array<{ label: string; value: number | string; icon: string; tone: string }> = stats
    ? isAdmin
      ? [
          { label: "用户（启用/总计）", value: `${stats.active_users}/${stats.total_users}`, icon: "users", tone: "bg-blue-500" },
          { label: "全局活动任务", value: stats.global_active_tasks, icon: "reply", tone: "bg-cyan-500" },
          { label: "累计任务", value: stats.total_tasks, icon: "dashboard", tone: "bg-emerald-500" },
          { label: "API Key / IM 连接", value: `${stats.total_api_keys}/${stats.total_connections}`, icon: "link", tone: "bg-amber-500" },
        ]
      : [
          { label: "进行中的任务", value: stats.my_active_tasks, icon: "reply", tone: "bg-blue-500" },
          { label: "累计任务", value: stats.my_total_tasks, icon: "dashboard", tone: "bg-cyan-500" },
          { label: "API Key", value: stats.my_api_keys, icon: "key", tone: "bg-emerald-500" },
          { label: "LLM 配置", value: stats.my_llm_configs, icon: "gateway", tone: "bg-amber-500" },
        ]
    : [];

  const copyCurlExample = async () => {
    const command = `curl "${window.location.origin}/v1/models" -H "Authorization: Bearer <API_KEY>"`;
    await copyText(command, "curl 命令");
  };

  return (
    <div className="relative space-y-5">
      {refreshing && data && (
        <div className="fixed left-0 right-0 top-0 z-50 h-0.5 overflow-hidden bg-primary/10">
          <div className="h-full w-1/3 animate-pulse bg-primary" />
        </div>
      )}
      <PageHeader
        title={isAdmin ? "监管控制台" : "控制台"}
        description="任务、协议与连接状态每 30 秒自动刷新。"
        dismissId="dashboard"
        actions={<Button variant="ghost" loading={refreshing} onClick={() => void load()}><Icon name="refresh" className="h-4 w-4" />立即刷新</Button>}
      />
      {error && <ErrorBanner message={error} />}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} values={values} />
        ))}
      </div>

      {data && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]">
          <div className="space-y-5">
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
              <TaskTimeline data={data} />
              <ProtocolDistribution data={data} />
            </div>
            <Card>
              <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
                <h2 className="text-sm font-medium text-slate-700">失败与超时</h2>
                <button onClick={() => navigate("/tasks")} className="text-xs text-primary">
                  查看全部
                </button>
              </div>
              <div className="divide-y divide-slate-100 p-2">
                {data.problem_tasks.map((task) => <TaskLink key={task.id} task={task} />)}
                {data.problem_tasks.length === 0 && (
                  <p className="px-3 py-8 text-center text-xs text-slate-400">暂无异常任务</p>
                )}
              </div>
            </Card>
          </div>

          <aside className="space-y-5">
            <Card>
              <div className="border-b border-slate-100 px-5 py-3 text-sm font-medium text-slate-700">
                临近截止
              </div>
              <div className="divide-y divide-slate-100 p-2">
                {data.urgent_tasks.map((task) => <TaskLink key={task.id} task={task} />)}
                {data.urgent_tasks.length === 0 && (
                  <p className="px-3 py-8 text-center text-xs text-slate-400">没有待处理任务</p>
                )}
              </div>
            </Card>

            <Card>
              <div className="border-b border-slate-100 px-5 py-3 text-sm font-medium text-slate-700">
                快速入口
              </div>
              <div className="grid grid-cols-2 gap-2 p-4 text-xs">
                {[
                  ["连接 IM", "/connections", "link"],
                  ["API 管理", "/api-keys", "key"],
                  ["模型广场", "/models", "cpu"],
                  ["LLM 管理", "/llm-configs", "gateway"],
                ].map(([label, path, icon]) => (
                  <button
                    key={path}
                    onClick={() => navigate(path)}
                    className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2.5 text-left text-slate-600 hover:border-primary/40 hover:text-primary"
                  >
                    <Icon name={icon} className="h-4 w-4" />
                    {label}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => void copyCurlExample()}
                  className="col-span-2 flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2.5 text-left text-slate-600 hover:border-primary/40 hover:text-primary"
                >
                  <Icon name="copy" className="h-4 w-4" />
                  复制 API 调用 curl
                </button>
              </div>
            </Card>

            {isAdmin && (
              <Card>
                <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
                  <span className="text-sm font-medium text-slate-700">IM 连接健康</span>
                  <button onClick={() => navigate("/connections")} className="text-xs text-primary">
                    管理
                  </button>
                </div>
                <div className="divide-y divide-slate-100 px-4">
                  {data.connection_health.map((connection) => (
                    <div key={connection.id} className="flex items-center gap-3 py-3 text-xs">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-slate-700">{connection.name}</span>
                        <span className="block truncate text-[11px] text-slate-400">
                          {connection.platform}
                          {connection.last_error ? ` · ${connection.last_error}` : ""}
                        </span>
                      </span>
                      <StatusBadge status={connection.state} />
                    </div>
                  ))}
                  {data.connection_health.length === 0 && (
                    <p className="py-8 text-center text-xs text-slate-400">暂无连接</p>
                  )}
                </div>
              </Card>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

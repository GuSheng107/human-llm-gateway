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
import { friendlyErrorMessage } from "../../utils/notify";

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

// 统计卡片配色：左侧渐变条 + 同色系图标底。
const STAT_TONES: Record<string, { bar: string; chip: string; glow: string }> = {
  blue: { bar: "from-blue-500 to-blue-400", chip: "bg-blue-50 text-blue-600", glow: "from-blue-50/70" },
  cyan: { bar: "from-cyan-500 to-cyan-400", chip: "bg-cyan-50 text-cyan-600", glow: "from-cyan-50/70" },
  emerald: { bar: "from-emerald-500 to-emerald-400", chip: "bg-emerald-50 text-emerald-600", glow: "from-emerald-50/70" },
  amber: { bar: "from-amber-500 to-amber-400", chip: "bg-amber-50 text-amber-600", glow: "from-amber-50/70" },
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
  const palette = STAT_TONES[tone] ?? STAT_TONES.blue;
  return (
    <Card className="group overflow-hidden transition-shadow duration-200 hover:shadow-lg">
      <div className={`relative flex min-h-28 items-center gap-3 bg-gradient-to-br ${palette.glow} to-transparent px-5 py-4`}>
        <span className={`absolute inset-y-0 left-0 w-1 bg-gradient-to-b ${palette.bar}`} />
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg shadow-sm transition-transform duration-200 group-hover:scale-105 ${palette.chip}`}>
          <Icon name={icon} className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className="leading-5">{label}</span>
            <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
              实时
            </span>
          </div>
          <span className="mt-1.5 block text-2xl font-semibold tabular-nums tracking-tight text-slate-800">
            {value}
          </span>
        </div>
        <div className={`hidden opacity-70 sm:block ${palette.chip.split(" ")[1]}`}>
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
      setError(friendlyErrorMessage(caught, "加载失败"));
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
  // 旧服务尚未重启或响应缺字段时，统计卡也要稳定显示 0，而不是 undefined。
  const activeTasks = stats?.active_tasks ?? 0;
  const availableModels = stats?.active_models ?? 0;
  const totalApiKeys = stats?.total_api_keys ?? 0;
  const cards: Array<{ label: string; value: number | string; icon: string; tone: string }> = stats
    ? [
        { label: "用户（启用/总计）", value: `${stats.active_users}/${stats.total_users}`, icon: "users", tone: "blue" },
        { label: "全站活动任务", value: activeTasks, icon: "reply", tone: "cyan" },
        { label: "累计任务", value: stats.total_tasks, icon: "dashboard", tone: "emerald" },
        { label: "可用模型 / API Key", value: `${availableModels} / ${totalApiKeys}`, icon: "link", tone: "amber" },
      ]
    : [];

  // 接入命令统一为 PowerShell 版本：curl.exe 规避 Windows PowerShell 5.1
  // 中 curl 被 Invoke-WebRequest 别名占用；反引号续行；JSON 内层双引号以
  // \" 转义（PowerShell 会剥离传给原生程序的参数中未转义的双引号）。
  const modelsCommand = `curl.exe "${window.location.origin}/v1/models" -H "Authorization: Bearer <API_KEY>"`;
  // PowerShell 反引号续行符单独提取：避免模板字符串尾部出现「转义反引号
  // 紧跟结束定界符」的连写，可读性差且容易写错。
  const BT = "`";
  const openAiCommand = [
    `curl.exe "${window.location.origin}/v1/chat/completions" ${BT}`,
    `  -H "Content-Type: application/json" ${BT}`,
    `  -H "Authorization: Bearer <API_KEY>" ${BT}`,
    `  -d '{\\"model\\": \\"glm-5.3\\", \\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"你好\\"}]}'`,
  ].join("\n");
  const anthropicCommand = [
    `curl.exe "${window.location.origin}/v1/messages" ${BT}`,
    `  -H "Content-Type: application/json" ${BT}`,
    `  -H "x-api-key: <API_KEY>" ${BT}`,
    `  -H "anthropic-version: 2023-06-01" ${BT}`,
    `  -d '{\\"model\\": \\"claude-sonnet-5\\", \\"max_tokens\\": 1024, \\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"你好\\"}]}'`,
  ].join("\n");

  const copyModelsCommand = async () => {
    await copyText(modelsCommand, "模型获取信息命令");
  };

  const copyOpenAiCommand = async () => {
    await copyText(openAiCommand, "OpenAI 调用命令");
  };

  const copyAnthropicCommand = async () => {
    await copyText(anthropicCommand, "Anthropic 调用命令");
  };

  return (
    <div className="relative space-y-5">
      {refreshing && data && (
        <div className="fixed left-0 right-0 top-0 z-50 h-0.5 overflow-hidden bg-primary/10">
          <div className="h-full w-1/3 animate-pulse bg-primary" />
        </div>
      )}
      <PageHeader
        title="控制台"
        description="30 秒自动刷新"
        actions={<Button variant="ghost" loading={refreshing} onClick={() => void load()}><Icon name="refresh" className="h-4 w-4" />立即刷新</Button>}
      />
      {error && <ErrorBanner message={error} />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} values={values} />
        ))}
      </div>

      {data && (
        <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]">
          <div className="flex h-full min-h-0 min-w-0 flex-col gap-5">
            <div className="grid grid-cols-1 shrink-0 gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
              <TaskTimeline data={data} />
              <ProtocolDistribution data={data} />
            </div>
            <Card className="flex min-h-[16rem] flex-1 flex-col">
              <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-3">
                <h2 className="text-sm font-medium text-slate-700">任务列表</h2>
                <button onClick={() => navigate("/tasks")} className="text-xs text-primary">
                  查看全部
                </button>
              </div>
              <div className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto p-2">
                {data.recent_tasks.map((task) => <TaskLink key={task.id} task={task} />)}
                {data.recent_tasks.length === 0 && (
                  <p className="grid h-full place-items-center px-3 text-center text-xs text-slate-400">暂无任务</p>
                )}
              </div>
            </Card>
          </div>

          <aside className="flex h-full min-h-0 min-w-0 flex-col gap-5">
            <Card>
              <div className="border-b border-slate-100 px-5 py-3 text-sm font-medium text-slate-700">
                快速入口
              </div>
              <div className="grid grid-cols-2 gap-2 p-4 text-xs">
                {[
                  ["连接 IM", "/connections", "link", "bg-cyan-50 text-cyan-600"],
                  ["API 管理", "/api-keys", "key", "bg-blue-50 text-blue-600"],
                  ["模型广场", "/models", "cpu", "bg-emerald-50 text-emerald-600"],
                  ["LLM 管理", "/llm-configs", "gateway", "bg-amber-50 text-amber-600"],
                ].map(([label, path, icon, chip]) => (
                  <button
                    key={path}
                    onClick={() => navigate(path)}
                    className="group flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2.5 text-left text-slate-600 transition-colors hover:border-primary/40 hover:text-primary"
                  >
                    <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md ${chip}`}>
                      <Icon name={icon} className="h-3.5 w-3.5" />
                    </span>
                    {label}
                  </button>
                ))}
              </div>
            </Card>

            <Card className="flex min-h-[16rem] flex-1 flex-col">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
                <span className="text-sm font-medium text-slate-700">接入指引</span>
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                    PowerShell
                  </span>
                  <span className="truncate rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-500">
                    {window.location.origin}
                  </span>
                </span>
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4 text-xs">
                {[
                  {
                    label: "获取模型列表",
                    protocol: "openai_chat",
                    command: modelsCommand,
                    run: copyModelsCommand,
                  },
                  {
                    label: "OpenAI 调用",
                    protocol: "openai_chat",
                    command: openAiCommand,
                    run: copyOpenAiCommand,
                  },
                  {
                    label: "Anthropic 调用",
                    protocol: "anthropic_messages",
                    command: anthropicCommand,
                    run: copyAnthropicCommand,
                  },
                ].map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => void item.run()}
                    className="group flex w-full items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-left transition-colors hover:border-primary/40 hover:bg-white"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: PROTOCOL_COLORS[item.protocol] ?? "#64748b" }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-slate-700">{item.label}</span>
                      <span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400 group-hover:text-slate-500">
                        {item.command}
                      </span>
                    </span>
                    <Icon
                      name="copy"
                      className="h-4 w-4 shrink-0 text-slate-300 transition-colors group-hover:text-primary"
                    />
                  </button>
                ))}
              </div>
            </Card>

          </aside>
        </div>
      )}
    </div>
  );
}

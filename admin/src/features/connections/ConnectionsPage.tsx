import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  bindingStatus,
  checkConnections,
  createBinding,
  deleteConnection,
  listConnections,
  listPlatforms,
  startConnection,
  stopConnection,
} from "../../api/connections";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type {
  BindingStatus as BindingStatusType,
  ConnectionCheckItem,
  ImConnection,
  PlatformSpec,
} from "../../types/gateway";
import { copyText } from "../../utils/clipboard";
import { useAuth } from "../auth/AuthContext";
import { ConnectionFormModal } from "./ConnectionFormModal";
import { QrLoginDrawer } from "./QrLoginDrawer";

const PAGE_SIZE = 20;

const PLATFORM_PRESENTATION: Record<
  string,
  { short: string; tone: string; action: "scan" | "configure" }
> = {
  wecom_ilink: {
    short: "微",
    tone: "border-emerald-200 bg-emerald-50 text-emerald-700",
    action: "scan",
  },
  wecom_aibot: {
    short: "企",
    tone: "border-blue-200 bg-blue-50 text-blue-700",
    action: "configure",
  },
  webhook: {
    short: "WH",
    tone: "border-amber-200 bg-amber-50 text-amber-700",
    action: "configure",
  },
  websocket: {
    short: "WS",
    tone: "border-violet-200 bg-violet-50 text-violet-700",
    action: "configure",
  },
  http_poll: {
    short: "HTTP",
    tone: "border-cyan-200 bg-cyan-50 text-cyan-700",
    action: "configure",
  },
};

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "-";
}

function platformPresentation(code: string) {
  return (
    PLATFORM_PRESENTATION[code] ?? {
      short: "IM",
      tone: "border-slate-200 bg-slate-50 text-slate-600",
      action: "configure" as const,
    }
  );
}

export function ConnectionsPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<ImConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformSpec[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<ImConnection | null | undefined>(undefined);
  const [qrConnection, setQrConnection] = useState<ImConnection | null>(null);
  const [healthReport, setHealthReport] = useState<ConnectionCheckItem[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [bindingConnection, setBindingConnection] = useState<ImConnection | null>(null);
  const [binding, setBinding] = useState<BindingStatusType | null>(null);
  const [bindingCommand, setBindingCommand] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const isAdmin = user?.role === "admin";

  const platformMap = useMemo(
    () => new Map(platforms.map((platform) => [platform.code, platform])),
    [platforms],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, platformList] = await Promise.all([
        listConnections(page, search),
        listPlatforms(),
      ]);
      setItems(list.items);
      setTotal(list.total);
      setPlatforms(platformList);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => void load(), [load]);

  useEffect(() => {
    if (!bindingConnection || !bindingCommand || binding?.bound) return;
    const timer = window.setInterval(() => {
      void bindingStatus(bindingConnection.id)
        .then((status) => {
          setBinding(status);
          if (status.bound) {
            notify("IM 连接绑定成功", "success");
            void load();
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [binding?.bound, bindingCommand, bindingConnection, load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const toggle = async (item: ImConnection) => {
    setBusyId(item.id);
    try {
      if (item.desired_running) await stopConnection(item.id);
      else await startConnection(item.id);
      notify(item.desired_running ? "连接已停止" : "连接已启动", "success");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败", "error");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (item: ImConnection) => {
    if (!(await confirmAction({ message: `确认删除连接「${item.name}」？` }))) return;
    try {
      await deleteConnection(item.id);
      notify("连接已删除", "success");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败", "error");
    }
  };

  const inspectConnections = async () => {
    setChecking(true);
    try {
      const report = await checkConnections();
      setHealthReport(report);
      const disabled = report.filter((item) => item.auto_disabled).length;
      notify(
        disabled ? `检查完成，已关闭 ${disabled} 个异常连接` : "检查完成，未发现需停用的异常连接",
        disabled ? "error" : "success",
      );
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "连接检查失败", "error");
    } finally {
      setChecking(false);
    }
  };

  const configureBinding = async (item: ImConnection) => {
    if (!item.desired_running) {
      notify("请先打开启用开关，再生成绑定命令", "info");
      return;
    }
    try {
      const created = await createBinding(item.id);
      setBindingConnection(item);
      setBindingCommand(created.binding_code);
      setBinding(await bindingStatus(item.id));
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "生成绑定命令失败", "error");
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="连接 IM"
        description="不同平台采用各自的连接方式；启用开关决定连接是否运行。后台看门狗会按配置周期检查异常状态。"
        dismissId="connections"
        actions={
          <>
            <Button variant="ghost" loading={checking} onClick={() => void inspectConnections()}>
              <Icon name="refresh" className="h-4 w-4" />
              检查连接
            </Button>
            {!isAdmin && (
              <Button onClick={() => setEditing(null)} disabled={platforms.length === 0}>
                <Icon name="plus" className="h-4 w-4" />
                新建连接
              </Button>
            )}
          </>
        }
      />

      <Card>
        <form onSubmit={submitSearch} className="flex flex-col gap-2 border-b border-slate-100 p-4 sm:flex-row">
          <div className="relative min-w-0 flex-1 sm:max-w-sm">
            <Icon name="search" className="pointer-events-none absolute top-2.5 left-3 h-4 w-4 text-slate-300" />
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              className="field-input pl-9"
              placeholder="搜索连接名称"
            />
          </div>
          <Button variant="ghost" type="submit">搜索</Button>
        </form>
        {error && <ErrorBanner message={error} className="m-4" />}

        <div className="space-y-3 p-4">
          {items.map((item) => {
            const presentation = platformPresentation(item.platform);
            const spec = platformMap.get(item.platform);
            return (
              <article key={item.id} className="group relative overflow-hidden rounded-lg border border-slate-200 bg-white transition hover:border-slate-300 hover:shadow-card">
                <span className={`absolute inset-y-0 left-0 w-1 ${item.state === "online" ? "bg-emerald-400" : item.state === "error" || item.state === "auth_required" ? "bg-red-400" : "bg-slate-200"}`} />
                <div className="flex flex-col gap-4 px-4 py-4 pl-5 lg:flex-row lg:items-center">
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-lg border text-xs font-semibold ${presentation.tone}`}>
                      {presentation.short}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate text-sm font-semibold text-slate-800">{item.name}</h2>
                        <StatusBadge status={item.state} fallback={item.state} />
                        <StatusBadge status={item.bound ? "bound" : "unbound"} />
                      </div>
                      <p className="mt-1 truncate text-xs text-slate-400">{item.platform_label} · {spec?.description ?? item.platform}</p>
                      <p className="mt-1 text-micro text-slate-400">
                        最近检查 {formatTime(item.last_health_at)}
                        {isAdmin && ` · 所有者 ${item.owner_username ?? "-"}`}
                        {item.retry_count > 0 && ` · 已重试 ${item.retry_count} 次`}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center justify-end gap-1.5 border-t border-slate-100 pt-3 lg:border-0 lg:pt-0">
                    <label className="mr-2 inline-flex cursor-pointer items-center gap-2 text-xs text-slate-500">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={item.desired_running}
                        aria-label={`${item.name}${item.desired_running ? "停用" : "启用"}`}
                        disabled={busyId === item.id}
                        onClick={() => void toggle(item)}
                        className={`relative h-5 w-9 rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:opacity-50 ${item.desired_running ? "bg-primary" : "bg-slate-300"}`}
                      >
                        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition ${item.desired_running ? "left-[18px]" : "left-0.5"}`} />
                      </button>
                      {item.desired_running ? "已启用" : "未启用"}
                    </label>
                    {!isAdmin && presentation.action === "scan" && (
                      <button type="button" onClick={() => setQrConnection(item)} className="rounded-md px-2.5 py-2 text-xs text-primary transition hover:bg-blue-50">扫码</button>
                    )}
                    {!isAdmin && presentation.action === "configure" && (
                      <button type="button" onClick={() => void configureBinding(item)} className="rounded-md px-2.5 py-2 text-xs text-primary transition hover:bg-blue-50">配置</button>
                    )}
                    {!isAdmin && (
                      <button type="button" onClick={() => setEditing(item)} className="rounded-md px-2.5 py-2 text-xs text-primary transition hover:bg-blue-50">编辑</button>
                    )}
                    <button type="button" onClick={() => void remove(item)} className="rounded-md px-2.5 py-2 text-xs text-red-500 transition hover:bg-red-50">删除</button>
                  </div>
                </div>
                {(item.last_error_code || item.last_error_message) && (
                  <div className="border-t border-red-100 bg-red-50/60 px-5 py-2 text-xs text-red-600">
                    {item.last_error_code ?? "connection_error"}：{item.last_error_message ?? "连接异常"}
                  </div>
                )}
              </article>
            );
          })}
          {!loading && items.length === 0 && (
            <div className="grid min-h-52 place-items-center rounded-lg border border-dashed border-slate-200 bg-slate-50/40 text-center">
              <div><Icon name="link" className="mx-auto h-7 w-7 text-slate-300" /><p className="mt-2 text-xs text-slate-400">暂无连接</p></div>
            </div>
          )}
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>

      {editing !== undefined && (
        <ConnectionFormModal
          platforms={platforms}
          connection={editing ?? undefined}
          onClose={() => setEditing(undefined)}
          onSaved={() => { setEditing(undefined); void load(); }}
        />
      )}

      {healthReport && (
        <Modal title="IM 连接检查结果" description={`共检查 ${healthReport.length} 个连接；异常且已启用的连接已自动关闭开关。`} onClose={() => setHealthReport(null)} width="max-w-4xl">
          <div className="max-h-[70vh] space-y-3 overflow-y-auto p-5">
            {healthReport.map((item) => (
              <section key={item.id} className={`rounded-lg border p-4 ${item.abnormal ? "border-red-200 bg-red-50/50" : "border-slate-200 bg-white"}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2"><h3 className="text-sm font-semibold text-slate-700">{item.name}</h3><StatusBadge status={item.state} fallback={item.state} /><StatusBadge status={item.bound ? "bound" : "unbound"} /></div>
                    <p className="mt-1 text-micro text-slate-400">{item.platform_label}{item.owner_username && ` · ${item.owner_username}`}</p>
                  </div>
                  <span className={`text-xs font-medium ${item.abnormal ? "text-red-600" : "text-emerald-600"}`}>{item.auto_disabled ? "异常，已自动停用" : item.abnormal ? "异常" : "检查正常"}</span>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 pt-3 text-xs md:grid-cols-4">
                  <div><dt className="text-slate-400">启用开关</dt><dd className="mt-0.5 text-slate-600">{item.desired_running ? "开启" : "关闭"}</dd></div>
                  <div><dt className="text-slate-400">运行实例</dt><dd className="mt-0.5 text-slate-600">{item.runtime.running ? "在线" : "未运行"}</dd></div>
                  <div><dt className="text-slate-400">重试次数</dt><dd className="mt-0.5 text-slate-600">{item.retry_count}</dd></div>
                  <div><dt className="text-slate-400">最近检查</dt><dd className="mt-0.5 text-slate-600">{formatTime(item.last_health_at)}</dd></div>
                  <div><dt className="text-slate-400">最近认证</dt><dd className="mt-0.5 text-slate-600">{formatTime(item.last_authenticated_at)}</dd></div>
                  <div><dt className="text-slate-400">下次重试</dt><dd className="mt-0.5 text-slate-600">{formatTime(item.next_retry_at)}</dd></div>
                  <div className="col-span-2"><dt className="text-slate-400">异常详情</dt><dd className="mt-0.5 break-words text-slate-600">{item.last_error_code ? `${item.last_error_code}：${item.last_error_message ?? "-"}` : "无"}</dd></div>
                </dl>
              </section>
            ))}
            {healthReport.length === 0 && <p className="py-12 text-center text-xs text-slate-400">暂无可检查的连接</p>}
          </div>
        </Modal>
      )}

      {bindingConnection && bindingCommand && (
        <Modal
          title={`配置连接 · ${bindingConnection.name}`}
          description="连接已启动后，请让需要绑定的用户在对应 IM 中发送以下完整命令。"
          onClose={() => { setBindingConnection(null); setBindingCommand(""); setBinding(null); }}
        >
          <div className="space-y-4 p-6">
            <div className="rounded-lg border border-blue-100 bg-blue-50 p-4">
              <p className="text-xs text-blue-600">发送指定消息</p>
              <div className="mt-2 flex items-center justify-between gap-3">
                <code className="break-all text-base font-semibold tracking-wide text-blue-800">{bindingCommand}</code>
                <Button onClick={() => void copyText(bindingCommand, "绑定命令")}><Icon name="copy" className="h-4 w-4" />复制</Button>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-xs"><span className="text-slate-500">绑定状态</span><StatusBadge status={binding?.bound ? "bound" : "waiting"} /></div>
            <p className="text-micro leading-5 text-slate-400">命令在当前绑定窗口内有效。系统每 2 秒刷新状态，收到并校验命令后会自动完成绑定。</p>
          </div>
        </Modal>
      )}

      <QrLoginDrawer
        connection={qrConnection}
        onClose={() => setQrConnection(null)}
        onSaved={() => { notify("扫码登录成功，参数已保存并完成绑定", "success"); void load(); }}
      />
    </div>
  );
}

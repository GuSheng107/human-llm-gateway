import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  createConnection,
  deleteConnection,
  fetchHealth,
  listConnections,
  listPlatforms,
  pollLogin,
  startBinding,
  startConnection,
  startLogin,
  stopConnection,
  applyConnection,
} from "../../api/connections";
import { Icon } from "../../icons";
import { Modal } from "../../components/feedback/Modal";
import { Drawer } from "../../components/feedback/Drawer";
import { notify } from "../../components/feedback/Toast";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { useAuth } from "../auth/AuthContext";
import type {
  BindingSnapshot,
  ConnectionCreated,
  HealthSnapshot,
  IMConnection,
  PlatformDefinition,
} from "../../types/connections";
import { EditConnectionDrawer } from "./EditConnectionDrawer";

const PLATFORM_COLORS: Record<string, string> = {
  wechat_ilink: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  wecom: "bg-sky-50 text-sky-700 ring-sky-200",
  webhook: "bg-orange-50 text-orange-700 ring-orange-200",
  websocket: "bg-violet-50 text-violet-700 ring-violet-200",
  http: "bg-cyan-50 text-cyan-700 ring-cyan-200",
};

const BINDING_LABELS: Record<string, string> = {
  unbound: "未绑定",
  waiting: "等待绑定",
  bound: "已绑定",
  expired: "已过期",
  locked: "已锁定",
};

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export function ConnectionsPage() {
  const { user } = useAuth();
  const location = useLocation();
  const isAdminView = location.pathname.startsWith("/admin");

  const [connections, setConnections] = useState<IMConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const [createOpen, setCreateOpen] = useState(false);
  const [platformId, setPlatformId] = useState("");
  const [botName, setBotName] = useState("");
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const [setup, setSetup] = useState<Record<string, unknown> | null>(null);
  const [binding, setBinding] = useState<BindingSnapshot | null>(null);
  const [bindingCountdown, setBindingCountdown] = useState(0);
  const [loginTarget, setLoginTarget] = useState<IMConnection | null>(null);
  const [loginSnapshot, setLoginSnapshot] = useState<HealthSnapshot | null>(null);
  const [health, setHealth] = useState<{ connection: IMConnection; snapshot: HealthSnapshot } | null>(null);
  const [editing, setEditing] = useState<IMConnection | null>(null);

  const platformMap = useMemo(() => new Map(platforms.map((p) => [p.id, p])), [platforms]);
  const selectedPlatform = platformMap.get(platformId);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextPlatforms, nextConnections] = await Promise.all([
        listPlatforms(),
        listConnections(),
      ]);
      setPlatforms(nextPlatforms);
      setConnections(nextConnections);
      if (!platformId && nextPlatforms.length) setPlatformId(nextPlatforms[0].id);
    } catch (error) {
      notify(error instanceof Error ? error.message : "连接数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [platformId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!document.hidden) return;
    return undefined;
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!selectedPlatform) return;
    const defaults: Record<string, string> = {};
    selectedPlatform.fields.forEach((field) => {
      defaults[field.key] = field.default == null ? "" : String(field.default);
    });
    setFormValues(defaults);
  }, [selectedPlatform]);

  useEffect(() => {
    if (!binding) return;
    setBindingCountdown(Math.max(0, Math.floor((new Date(binding.expires_at).getTime() - Date.now()) / 1000)));
    const timer = window.setInterval(() => {
      setBindingCountdown((current) => {
        if (current <= 1) {
          window.clearInterval(timer);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [binding]);

  useEffect(() => {
    if (!loginTarget) return;
    let cancelled = false;
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      try {
        const snapshot = await pollLogin(loginTarget.id);
        if (cancelled) return;
        setLoginSnapshot(snapshot);
        const state = snapshot.login_state ?? snapshot.state ?? "";
        if (state === "connected") {
          stopped = true;
          void load();
          return;
        }
        if (state === "error" || state === "expired") {
          stopped = true;
          return;
        }
        window.setTimeout(poll, 1500);
      } catch (error) {
        if (!cancelled) {
          notify(error instanceof Error ? error.message : "登录状态读取失败");
          stopped = true;
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      stopped = true;
    };
  }, [loginTarget, load]);

  const filtered = useMemo(() => connections.filter((item) => {
    const query = search.trim().toLowerCase();
    const matchesQuery = !query || [item.name, item.owner_name, item.bound_user_id]
      .some((value) => value.toLowerCase().includes(query));
    return matchesQuery
      && (platformFilter === "all" || item.platform === platformFilter)
      && (statusFilter === "all" || item.status === statusFilter);
  }), [connections, platformFilter, search, statusFilter]);

  const submitCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedPlatform || !botName.trim()) return;
    setSubmitting(true);
    try {
      const created = await createConnection({
        name: botName.trim(),
        platform: selectedPlatform.id,
        config: formValues,
      });
      setCreateOpen(false);
      setBotName("");
      if (Object.keys(created.setup).length) setSetup(created.setup);
      notify("连接已创建，请继续完成身份绑定");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runAction = async (connection: IMConnection, action: "start" | "stop" | "apply") => {
    try {
      if (action === "start") await startConnection(connection.id);
      else if (action === "stop") await stopConnection(connection.id);
      else await applyConnection(connection.id);
      notify(action === "start" ? "已启动" : action === "stop" ? "已停止" : "配置已应用并重启该连接");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "操作失败");
    }
  };

  const beginBinding = async (connection: IMConnection) => {
    try {
      setBinding(await startBinding(connection.id));
    } catch (error) {
      notify(error instanceof Error ? error.message : "绑定流程启动失败");
    }
  };

  const beginLogin = async (connection: IMConnection) => {
    setLoginTarget(connection);
    setLoginSnapshot({ status: "connecting", login_state: "pending" });
    try {
      setLoginSnapshot(await startLogin(connection.id));
    } catch (error) {
      notify(error instanceof Error ? error.message : "扫码登录启动失败");
      setLoginTarget(null);
    }
  };

  const inspectHealth = async (connection: IMConnection) => {
    try {
      setHealth({ connection, snapshot: await fetchHealth(connection.id) });
    } catch (error) {
      notify(error instanceof Error ? error.message : "健康状态读取失败");
    }
  };

  const remove = async (connection: IMConnection) => {
    if (!window.confirm(`确定删除连接「${connection.name}」？连接会立即停止并解除 Key 绑定。`)) return;
    try {
      await deleteConnection(connection.id);
      notify("连接已删除");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除失败");
    }
  };

  const can = (connection: IMConnection, action: string) =>
    connection.allowed_actions.includes(action) && (!isAdminView || action === "stop" || action === "view_logs");

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-5 w-1 rounded-full bg-[#409eff]" />
            <h1 className="text-lg font-semibold text-slate-800">{isAdminView ? "连接监管" : "我的连接"}</h1>
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">
            {isAdminView
              ? "管理员仅监管用户连接，可强制停止异常连接；不能查看凭据或修改配置。"
              : "创建并管理自己的 IM 连接：配置 → 启动 → 扫码/绑定 → 在线。修改配置后需应用重启该连接。"}
          </p>
        </div>
        {!isAdminView && (
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white shadow-sm transition hover:bg-[#337ecc]"
          >
            <Icon name="plus" className="h-4 w-4" />
            创建连接
          </button>
        )}
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ["连接总数", connections.length, "text-slate-800"],
          ["在线", connections.filter((c) => c.status === "online").length, "text-emerald-600"],
          ["已绑定", connections.filter((c) => c.binding_status === "bound").length, "text-[#409eff]"],
          ["需处理", connections.filter((c) => c.status === "error" || c.status === "pending_restart" || c.binding_status === "locked").length, "text-red-500"],
        ].map(([label, value, tone]) => (
          <div key={String(label)} className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
            <div className="text-[11px] text-slate-400">{label}</div>
            <div className={`mt-2 font-mono text-2xl font-semibold ${tone}`}>{value}</div>
          </div>
        ))}
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div className="relative w-full md:max-w-xs">
            <Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索名称、所属用户或 userid"
              className="h-9 w-full rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 text-xs outline-none transition placeholder:text-slate-400 focus:border-[#409eff] focus:bg-white focus:ring-2 focus:ring-[#409eff]/10"
            />
          </div>
          <div className="flex gap-2">
            <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-[#409eff]">
              <option value="all">全部平台</option>
              {platforms.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-[#409eff]">
              <option value="all">全部状态</option>
              <option value="online">在线</option>
              <option value="connecting">连接中</option>
              <option value="offline">离线</option>
              <option value="error">异常</option>
              <option value="stopped">已停止</option>
              <option value="pending_restart">待重启</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1050px] border-collapse text-left">
            <thead>
              <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
                <th className="px-5 py-3">连接 / 平台</th>
                {isAdminView && <th className="px-4 py-3">所属用户</th>}
                <th className="px-4 py-3">连接状态</th>
                <th className="px-4 py-3">绑定状态</th>
                <th className="px-4 py-3">最近消息</th>
                <th className="px-5 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">正在读取连接状态…</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={6} className="px-5 py-16 text-center">
                  <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-slate-50 text-slate-300"><Icon name="link" className="h-5 w-5" /></div>
                  <p className="mt-3 text-xs text-slate-400">没有符合条件的连接</p>
                </td></tr>
              ) : filtered.map((item) => {
                const definition = platformMap.get(item.platform);
                const canLogin = definition?.capabilities.includes("login") && !isAdminView;
                return (
                  <tr key={item.id} className="text-xs transition hover:bg-[#f7fbff]">
                    <td className="px-5 py-4">
                      <div className="font-medium text-slate-800">{item.name}</div>
                      <div className="mt-1.5 flex items-center gap-2">
                        <span className={`inline-flex rounded-md px-2 py-1 text-[11px] font-medium ring-1 ring-inset ${PLATFORM_COLORS[item.platform] ?? "bg-slate-50 text-slate-600 ring-slate-200"}`}>
                          {definition?.label ?? item.platform}
                        </span>
                        <span className="font-mono text-[10px] text-slate-300">#{item.id}</span>
                        {item.restart_required && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">待重启 v{item.config_version}</span>}
                      </div>
                    </td>
                    {isAdminView && (
                      <td className="px-4 py-4">
                        <div className="font-medium text-slate-700">{item.owner_name}</div>
                        <div className="mt-1 text-[10px] text-slate-400">UID {item.owner_id}</div>
                      </td>
                    )}
                    <td className="px-4 py-4">
                      <StatusBadge status={item.status} />
                      {item.error_code && <div className="mt-1 font-mono text-[10px] text-red-400">{item.error_code}</div>}
                    </td>
                    <td className="px-4 py-4">
                      <div className={`font-medium ${item.binding_status === "bound" ? "text-emerald-600" : item.binding_status === "waiting" ? "text-amber-600" : item.binding_status === "locked" ? "text-red-600" : "text-slate-500"}`}>
                        {BINDING_LABELS[item.binding_status] ?? item.binding_status}
                      </div>
                      <div className="mt-1 max-w-52 truncate font-mono text-[10px] text-slate-400" title={item.bound_user_id}>
                        {item.bound_user_id || "尚未记录平台 userid"}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-slate-500">{formatTime(item.last_seen_at)}</td>
                    <td className="px-5 py-4">
                      <div className="flex flex-wrap items-center justify-end gap-1.5">
                        <button type="button" onClick={() => void inspectHealth(item)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 transition hover:border-[#a0cfff] hover:text-[#409eff]">状态</button>
                        {canLogin && can(item, "regenerate_binding") && item.binding_status !== "bound" && (
                          <button type="button" onClick={() => void beginLogin(item)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 transition hover:border-[#a0cfff] hover:text-[#409eff]">扫码</button>
                        )}
                        {!isAdminView && can(item, "regenerate_binding") && (
                          <button type="button" onClick={() => void beginBinding(item)} className="rounded border border-[#b3d8ff] bg-[#ecf5ff] px-2.5 py-1.5 text-[11px] text-[#409eff] transition hover:bg-[#d9ecff]">
                            {item.binding_status === "bound" ? "重新绑定" : "绑定"}
                          </button>
                        )}
                        {!isAdminView && can(item, "apply") && (
                          <button type="button" onClick={() => void runAction(item, "apply")} className="rounded bg-amber-50 border border-amber-200 px-2.5 py-1.5 text-[11px] text-amber-600 transition hover:bg-amber-100">应用重启</button>
                        )}
                        {can(item, "stop") && (
                          <button type="button" onClick={() => void runAction(item, "stop")} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">停止</button>
                        )}
                        {can(item, "start") && (
                          <button type="button" onClick={() => void runAction(item, "start")} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">启动</button>
                        )}
                        {!isAdminView && can(item, "delete") && (
                          <button type="button" onClick={() => void remove(item)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 transition hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button>
                        )}
                        {!isAdminView && (
                          <button type="button" onClick={() => setEditing(item)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 transition hover:border-[#a0cfff] hover:text-[#409eff]">编辑</button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <footer className="flex items-center justify-between border-t border-slate-100 bg-slate-50/50 px-5 py-3 text-[10px] text-slate-400">
          <span>共 {filtered.length} 条记录 · 每 10 秒自动刷新</span>
          <span>凭据仅所属用户可见 · 管理员只监管</span>
        </footer>
      </section>

      {createOpen && selectedPlatform && (
        <Modal title="创建 IM 连接" description="凭据只用于连接你自己的 Bot；创建后还需完成绑定。" onClose={() => setCreateOpen(false)}>
          <form onSubmit={submitCreate} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">连接名称</span>
              <input required maxLength={120} value={botName} onChange={(event) => setBotName(event.target.value)} placeholder="例如：我的企微回复助手" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">IM 平台</span>
              <select value={platformId} onChange={(event) => setPlatformId(event.target.value)} className="field-input">
                {platforms.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
              <p className="mt-1.5 text-[11px] leading-5 text-slate-400">{selectedPlatform.description}</p>
            </label>
            {selectedPlatform.fields.map((field) => (
              <label className="block" key={field.key}>
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  {field.label}{field.required && <span className="ml-1 text-red-400">*</span>}
                </span>
                {field.kind === "json" ? (
                  <textarea value={formValues[field.key] ?? ""} onChange={(event) => setFormValues({ ...formValues, [field.key]: event.target.value })} placeholder={field.placeholder || '{"Authorization":"Bearer ..."}'} className="field-input min-h-20 resize-y font-mono" />
                ) : (
                  <input
                    required={field.required}
                    type={field.secret ? "password" : field.kind === "number" ? "number" : "text"}
                    value={formValues[field.key] ?? ""}
                    onChange={(event) => setFormValues({ ...formValues, [field.key]: event.target.value })}
                    placeholder={field.placeholder}
                    className="field-input"
                  />
                )}
              </label>
            ))}
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setCreateOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "正在创建…" : "创建连接"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {binding && (
        <Modal
          title="发送绑定消息"
          description={bindingCountdown > 0 ? `剩余 ${bindingCountdown} 秒，超时可原地重新生成。` : "绑定码已过期，可原地重新生成。"}
          onClose={() => { setBinding(null); void load(); }}
        >
          <div className="px-6 py-6">
            <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-4">
              <div className="text-[11px] text-blue-500">一次性绑定命令</div>
              <div className="mt-2 flex items-center justify-between gap-3">
                <code className="font-mono text-lg font-semibold tracking-wider text-blue-700">{binding.command}</code>
                <button type="button" onClick={() => { void navigator.clipboard.writeText(binding.command); notify("绑定命令已复制"); }} className="rounded-md border border-blue-200 bg-white p-2 text-blue-500 hover:bg-blue-50">
                  <Icon name="copy" className="h-4 w-4" />
                </button>
              </div>
              {bindingCountdown > 0 && (
                <div className="mt-3 h-1 overflow-hidden rounded-full bg-blue-100">
                  <div className="h-full bg-blue-400 transition-all duration-1000" style={{ width: `${(bindingCountdown / 300) * 100}%` }} />
                </div>
              )}
            </div>
            <ol className="mt-5 list-decimal space-y-2 pl-5 text-xs leading-5 text-slate-500">
              <li>打开对应 IM 软件，找到你刚创建的 Bot。</li>
              <li>发送完整命令，不要把绑定码发给其他人。</li>
              <li>绑定成功后关闭本窗口，列表将自动刷新。</li>
            </ol>
            {bindingCountdown === 0 && (
              <button type="button" onClick={() => binding && void beginBinding(loginTarget ?? connections.find((c) => c.id === binding.connection_id) ?? connections[0])} className="mt-4 w-full rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc]">
                重新生成绑定码
              </button>
            )}
          </div>
        </Modal>
      )}

      {loginTarget && (
        <Modal title={`扫码登录 · ${loginTarget.name}`} description="二维码仅用于连接你自己的 Bot。" onClose={() => { setLoginTarget(null); setLoginSnapshot(null); }}>
          <div className="grid min-h-80 place-items-center px-6 py-6 text-center">
            {loginSnapshot?.qr ? (
              <>
                <img src={loginSnapshot.qr} alt="扫码登录二维码" className="h-56 w-56 rounded-lg border border-slate-100 bg-white p-2 shadow-sm" />
                <p className="mt-4 text-xs text-slate-500">{loginSnapshot.login_state === "scanned" ? "已扫码，正在确认…" : "请使用 IM 扫描二维码"}</p>
              </>
            ) : loginSnapshot?.error ? (
              <div>
                <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-red-50 text-red-500"><Icon name="warning" className="h-6 w-6" /></div>
                <p className="mt-3 text-sm text-red-500">{loginSnapshot.error}</p>
                <button type="button" onClick={() => void beginLogin(loginTarget)} className="mt-4 rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc]">重新扫码</button>
              </div>
            ) : (
              <div>
                <span className="mx-auto block h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" />
                <p className="mt-4 text-xs text-slate-400">正在生成登录二维码…</p>
              </div>
            )}
          </div>
        </Modal>
      )}

      {setup && (
        <Modal title="保存连接信息" description="敏感 Token 只在创建完成时展示，请立即保存。" onClose={() => setSetup(null)}>
          <div className="space-y-3 px-6 py-5">
            {Object.entries(setup).map(([key, value]) => (
              <div key={key} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-400">{key}</div>
                <div className="mt-1 flex items-center justify-between gap-3">
                  <code className="break-all font-mono text-xs text-slate-700">{String(value)}</code>
                  <button type="button" onClick={() => void navigator.clipboard.writeText(String(value))} className="shrink-0 text-slate-400 hover:text-[#409eff]">
                    <Icon name="copy" className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </Modal>
      )}

      {health && (
        <Drawer title={`运行状态 · ${health.connection.name}`} description="结构化健康快照" onClose={() => setHealth(null)}>
          <div className="space-y-4 px-6 py-5">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-500">连接器健康状态</span>
              <StatusBadge status={health.snapshot.status} />
            </div>
            <dl className="divide-y divide-slate-100 rounded-md border border-slate-200">
              {Object.entries(health.snapshot)
                .filter(([key]) => key !== "qr")
                .map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between px-4 py-3 text-xs">
                    <dt className="text-slate-400">{key}</dt>
                    <dd className="max-w-[60%] truncate font-mono text-slate-700">{String(value)}</dd>
                  </div>
                ))}
            </dl>
          </div>
        </Drawer>
      )}

      {editing && (
        <EditConnectionDrawer
          connection={editing}
          platform={platformMap.get(editing.platform) ?? null}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); void load(); }}
        />
      )}
    </div>
  );
}

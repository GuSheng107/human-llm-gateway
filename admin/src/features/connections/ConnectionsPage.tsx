import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { Icon } from "../../icons";
import type {
  BindingSnapshot,
  ConnectionCreated,
  CurrentUser,
  HealthSnapshot,
  IMConnection,
  PlatformDefinition,
} from "../../types";

const statusMeta: Record<string, { label: string; className: string; dot: string }> = {
  online: { label: "在线", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  connecting: { label: "连接中", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500 animate-pulse" },
  offline: { label: "离线", className: "border-slate-200 bg-slate-50 text-slate-500", dot: "bg-slate-400" },
  error: { label: "异常", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  disabled: { label: "已删除", className: "border-slate-200 bg-slate-50 text-slate-400", dot: "bg-slate-300" },
};

const bindingLabels: Record<string, string> = {
  unbound: "未绑定身份",
  binding: "等待绑定消息",
  bound: "身份已绑定",
  expired: "绑定码已过期",
};

const platformColors: Record<string, string> = {
  wechat_ilink: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  wecom: "bg-sky-50 text-sky-700 ring-sky-200",
  webhook: "bg-orange-50 text-orange-700 ring-orange-200",
  websocket: "bg-violet-50 text-violet-700 ring-violet-200",
  http: "bg-cyan-50 text-cyan-700 ring-cyan-200",
};

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function StatusBadge({ status }: { status: string }) {
  const meta = statusMeta[status] ?? statusMeta.offline;
  return <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.className}`}><span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />{meta.label}</span>;
}

function PlatformBadge({ id, label }: { id: string; label: string }) {
  const tone = platformColors[id] ?? "bg-slate-50 text-slate-600 ring-slate-200";
  return <span className={`inline-flex rounded-md px-2 py-1 text-[11px] font-medium ring-1 ring-inset ${tone}`}>{label}</span>;
}

function Modal({ title, description, onClose, children, width = "max-w-xl" }: { title: string; description?: string; onClose: () => void; children: ReactNode; width?: string }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/35 p-4 backdrop-blur-[1px]" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className={`w-full ${width} overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="flex items-start justify-between border-b border-slate-100 px-6 py-5">
          <div><h2 className="text-base font-semibold text-slate-800">{title}</h2>{description && <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>}</div>
          <button type="button" onClick={onClose} className="rounded p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"><Icon name="close" className="h-4 w-4" /></button>
        </header>
        {children}
      </section>
    </div>
  );
}

interface Props {
  user: CurrentUser;
  refreshKey: number;
  notify: (message: string) => void;
}

export function ConnectionsPage({ user, refreshKey, notify }: Props) {
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
  const [binding, setBinding] = useState<BindingSnapshot | null>(null);
  const [loginTarget, setLoginTarget] = useState<IMConnection | null>(null);
  const [loginSnapshot, setLoginSnapshot] = useState<HealthSnapshot | null>(null);
  const [setup, setSetup] = useState<Record<string, unknown> | null>(null);
  const [health, setHealth] = useState<{ connection: IMConnection; snapshot: HealthSnapshot } | null>(null);

  const platformMap = useMemo(() => new Map(platforms.map((item) => [item.id, item])), [platforms]);
  const selectedPlatform = platformMap.get(platformId);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextPlatforms, nextConnections] = await Promise.all([
        api<PlatformDefinition[]>("/api/im-platforms"),
        api<IMConnection[]>("/api/im-connections"),
      ]);
      setPlatforms(nextPlatforms);
      setConnections(nextConnections);
      if (!platformId && nextPlatforms.length) setPlatformId(nextPlatforms[0].id);
    } catch (error) {
      notify(error instanceof Error ? error.message : "连接数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [notify, platformId]);

  useEffect(() => { void load(); }, [load, refreshKey]);

  useEffect(() => {
    if (!selectedPlatform) return;
    const defaults: Record<string, string> = {};
    selectedPlatform.fields.forEach((field) => { defaults[field.key] = field.default == null ? "" : String(field.default); });
    setFormValues(defaults);
  }, [selectedPlatform]);

  useEffect(() => {
    if (!loginTarget) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const snapshot = await api<HealthSnapshot>(`/api/im-connections/${loginTarget.id}/login`);
        if (!cancelled) setLoginSnapshot(snapshot);
        if (!cancelled && !["connected", "error"].includes(snapshot.login_state ?? snapshot.state ?? "")) window.setTimeout(poll, 1500);
        if (!cancelled && (snapshot.login_state ?? snapshot.state) === "connected") void load();
      } catch (error) {
        if (!cancelled) notify(error instanceof Error ? error.message : "登录状态读取失败");
      }
    };
    void poll();
    return () => { cancelled = true; };
  }, [loginTarget, load, notify]);

  const filtered = useMemo(() => connections.filter((item) => {
    const query = search.trim().toLowerCase();
    const matchesQuery = !query || [item.name, item.owner_name, item.bound_user_id].some((value) => value.toLowerCase().includes(query));
    return matchesQuery && (platformFilter === "all" || item.platform === platformFilter) && (statusFilter === "all" || item.status === statusFilter);
  }), [connections, platformFilter, search, statusFilter]);

  const counts = useMemo(() => ({
    total: connections.length,
    online: connections.filter((item) => item.status === "online").length,
    bound: connections.filter((item) => item.binding_status === "bound").length,
    issues: connections.filter((item) => item.status === "error" || item.binding_status === "expired").length,
  }), [connections]);

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedPlatform || !botName.trim()) return;
    setSubmitting(true);
    try {
      const created = await api<ConnectionCreated>("/api/im-connections", {
        method: "POST",
        body: JSON.stringify({ name: botName.trim(), platform: selectedPlatform.id, config: formValues }),
      });
      setCreateOpen(false);
      setBotName("");
      if (Object.keys(created.setup).length) setSetup(created.setup);
      notify("Bot 已创建，请继续完成身份绑定");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Bot 创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runAction = async (connection: IMConnection, action: "start" | "stop") => {
    try {
      await api(`/api/im-connections/${connection.id}/${action}`, { method: "POST" });
      notify(action === "start" ? "Bot 已启动" : "Bot 已停止");
      await load();
    } catch (error) { notify(error instanceof Error ? error.message : "操作失败"); }
  };

  const beginBinding = async (connection: IMConnection) => {
    try { setBinding(await api<BindingSnapshot>(`/api/im-connections/${connection.id}/binding`, { method: "POST" })); await load(); }
    catch (error) { notify(error instanceof Error ? error.message : "绑定流程启动失败"); }
  };

  const beginLogin = async (connection: IMConnection) => {
    setLoginTarget(connection);
    setLoginSnapshot({ status: "connecting", login_state: "pending" });
    try { setLoginSnapshot(await api<HealthSnapshot>(`/api/im-connections/${connection.id}/login`, { method: "POST" })); }
    catch (error) { notify(error instanceof Error ? error.message : "扫码登录启动失败"); setLoginTarget(null); }
  };

  const inspectHealth = async (connection: IMConnection) => {
    try { setHealth({ connection, snapshot: await api<HealthSnapshot>(`/api/im-connections/${connection.id}/health`) }); }
    catch (error) { notify(error instanceof Error ? error.message : "健康状态读取失败"); }
  };

  const remove = async (connection: IMConnection) => {
    if (!window.confirm(`确定删除 Bot「${connection.name}」？连接会立即停止，记录将在列表中隐藏。`)) return;
    try { await api(`/api/im-connections/${connection.id}`, { method: "DELETE" }); notify("Bot 已删除"); await load(); }
    catch (error) { notify(error instanceof Error ? error.message : "删除失败"); }
  };

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2"><span className="h-5 w-1 rounded-full bg-[#409eff]" /><h1 className="text-lg font-semibold text-slate-800">{user.role === "admin" ? "全部用户 Bot" : "我的 IM Bot"}</h1></div>
          <p className="mt-2 text-xs leading-5 text-slate-400">{user.role === "admin" ? "管理员仅监管用户创建的连接，可检查状态、停止或删除，不代替用户创建和绑定。" : "每个 Bot 都属于你。接入凭据后，请向自己的 Bot 发送一次性绑定码确认 IM 身份。"}</p>
        </div>
        {user.role === "user" && <button type="button" onClick={() => setCreateOpen(true)} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white shadow-sm transition hover:bg-[#337ecc]"><Icon name="plus" className="h-4 w-4" />创建 Bot</button>}
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["Bot 总数", counts.total, "text-slate-800"], ["当前在线", counts.online, "text-emerald-600"], ["身份已绑定", counts.bound, "text-[#409eff]"], ["需要处理", counts.issues, "text-red-500"]].map(([label, value, tone]) => (
          <div key={String(label)} className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm"><div className="text-[11px] text-slate-400">{label}</div><div className={`mt-2 font-mono text-2xl font-semibold ${tone}`}>{value}</div></div>
        ))}
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div className="relative w-full md:max-w-xs"><Icon name="search" className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Bot、所属用户或 userid" className="h-9 w-full rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 text-xs outline-none transition placeholder:text-slate-400 focus:border-[#409eff] focus:bg-white focus:ring-2 focus:ring-[#409eff]/10" /></div>
          <div className="flex gap-2"><select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-[#409eff]"><option value="all">全部平台</option>{platforms.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-[#409eff]"><option value="all">全部状态</option><option value="online">在线</option><option value="connecting">连接中</option><option value="offline">离线</option><option value="error">异常</option></select></div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-left">
            <thead><tr className="bg-slate-50 text-[11px] font-medium text-slate-500"><th className="px-5 py-3">Bot / 平台</th>{user.role === "admin" && <th className="px-4 py-3">所属用户</th>}<th className="px-4 py-3">连接状态</th><th className="px-4 py-3">IM 身份绑定</th><th className="px-4 py-3">最近消息</th><th className="px-5 py-3 text-right">操作</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">正在读取连接状态…</td></tr> : filtered.length === 0 ? <tr><td colSpan={6} className="px-5 py-16 text-center"><div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-slate-50 text-slate-300"><Icon name="link" className="h-5 w-5" /></div><p className="mt-3 text-xs text-slate-400">没有符合条件的 Bot</p></td></tr> : filtered.map((item) => {
                const definition = platformMap.get(item.platform);
                const canLogin = definition?.capabilities.includes("login");
                return <tr key={item.id} className="group text-xs transition hover:bg-[#f7fbff]">
                  <td className="px-5 py-4"><div className="font-medium text-slate-800">{item.name}</div><div className="mt-1.5 flex items-center gap-2"><PlatformBadge id={item.platform} label={definition?.label ?? item.platform} /><span className="font-mono text-[10px] text-slate-300">#{item.id}</span></div></td>
                  {user.role === "admin" && <td className="px-4 py-4"><div className="font-medium text-slate-700">{item.owner_name}</div><div className="mt-1 text-[10px] text-slate-400">UID {item.owner_id}</div></td>}
                  <td className="px-4 py-4"><StatusBadge status={item.status} />{item.last_error && <div className="mt-1.5 max-w-48 truncate text-[10px] text-red-400" title={item.last_error}>{item.last_error}</div>}</td>
                  <td className="px-4 py-4"><div className={`font-medium ${item.binding_status === "bound" ? "text-emerald-600" : item.binding_status === "binding" ? "text-amber-600" : "text-slate-500"}`}>{bindingLabels[item.binding_status]}</div><div className="mt-1 max-w-52 truncate font-mono text-[10px] text-slate-400" title={item.bound_user_id}>{item.bound_user_id || "尚未记录平台 userid"}</div></td>
                  <td className="px-4 py-4 text-slate-500">{formatTime(item.last_seen_at)}</td>
                  <td className="px-5 py-4"><div className="flex items-center justify-end gap-1.5"><button type="button" onClick={() => void inspectHealth(item)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 transition hover:border-[#a0cfff] hover:text-[#409eff]">状态</button>{user.role === "user" && canLogin && <button type="button" onClick={() => void beginLogin(item)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 transition hover:border-[#a0cfff] hover:text-[#409eff]">扫码</button>}{user.role === "user" && <button type="button" onClick={() => void beginBinding(item)} className="rounded border border-[#b3d8ff] bg-[#ecf5ff] px-2.5 py-1.5 text-[11px] text-[#409eff] transition hover:bg-[#d9ecff]">{item.binding_status === "bound" ? "重新绑定" : "绑定"}</button>}{item.status === "online" || item.status === "connecting" ? <button type="button" onClick={() => void runAction(item, "stop")} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">停止</button> : <button type="button" onClick={() => void runAction(item, "start")} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">启动</button>}<button type="button" onClick={() => void remove(item)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 transition hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button></div></td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>
        <footer className="flex items-center justify-between border-t border-slate-100 bg-slate-50/50 px-5 py-3 text-[10px] text-slate-400"><span>共 {filtered.length} 条记录</span><span>Bot 凭据由所属用户维护 · 管理员只监管</span></footer>
      </section>

      {createOpen && selectedPlatform && <Modal title="创建自己的 IM Bot" description="凭据只用于连接你自己的 Bot；创建完成后还需要发送绑定码确认 userid。" onClose={() => setCreateOpen(false)}>
        <form onSubmit={submitCreate} className="space-y-4 px-6 py-5"><label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">Bot 名称</span><input required maxLength={120} value={botName} onChange={(event) => setBotName(event.target.value)} placeholder="例如：我的企微回复助手" className="field-input" /></label><label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">IM 平台</span><select value={platformId} onChange={(event) => setPlatformId(event.target.value)} className="field-input">{platforms.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select><p className="mt-1.5 text-[11px] leading-5 text-slate-400">{selectedPlatform.description}</p></label>{selectedPlatform.fields.map((field) => <label className="block" key={field.key}><span className="mb-1.5 block text-xs font-medium text-slate-600">{field.label}{field.required && <span className="ml-1 text-red-400">*</span>}</span>{field.kind === "json" ? <textarea value={formValues[field.key] ?? ""} onChange={(event) => setFormValues({ ...formValues, [field.key]: event.target.value })} placeholder={field.placeholder || '{"Authorization":"Bearer ..."}'} className="field-input min-h-20 resize-y font-mono" /> : <input required={field.required} type={field.secret ? "password" : field.kind === "number" ? "number" : "text"} value={formValues[field.key] ?? ""} onChange={(event) => setFormValues({ ...formValues, [field.key]: event.target.value })} placeholder={field.placeholder} className="field-input" />}</label>)}<div className="flex justify-end gap-2 border-t border-slate-100 pt-4"><button type="button" onClick={() => setCreateOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button><button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">{submitting ? "正在创建…" : "创建 Bot"}</button></div></form>
      </Modal>}

      {binding && <Modal title="发送绑定消息" description={`请在 ${new Date(binding.expires_at).toLocaleTimeString("zh-CN")} 前，用你本人账号给 Bot 发送下方完整命令。`} onClose={() => { setBinding(null); void load(); }}><div className="px-6 py-6"><div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-4"><div className="text-[11px] text-blue-500">一次性绑定命令</div><div className="mt-2 flex items-center justify-between gap-3"><code className="font-mono text-lg font-semibold tracking-wider text-blue-700">{binding.command}</code><button type="button" onClick={() => { void navigator.clipboard.writeText(binding.command); notify("绑定命令已复制"); }} className="rounded-md border border-blue-200 bg-white p-2 text-blue-500 hover:bg-blue-50"><Icon name="copy" className="h-4 w-4" /></button></div></div><ol className="mt-5 list-decimal space-y-2 pl-5 text-xs leading-5 text-slate-500"><li>打开对应 IM 软件，找到你刚创建的 Bot。</li><li>发送完整命令，不要把绑定码发给其他人。</li><li>Bot 回复“绑定成功”后关闭本窗口并刷新列表。</li></ol></div></Modal>}

      {loginTarget && <Modal title={`扫码登录 · ${loginTarget.name}`} description="二维码仅用于连接你自己的微信 Bot。" onClose={() => { setLoginTarget(null); setLoginSnapshot(null); }}><div className="grid min-h-80 place-items-center px-6 py-6 text-center">{loginSnapshot?.qr ? <><img src={loginSnapshot.qr} alt="微信扫码登录二维码" className="h-56 w-56 rounded-lg border border-slate-100 bg-white p-2 shadow-sm" /><p className="mt-4 text-xs text-slate-500">{loginSnapshot.login_state === "scanned" ? "已扫码，正在确认…" : "请使用微信扫描二维码"}</p></> : loginSnapshot?.error ? <div><div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-red-50 text-red-500">!</div><p className="mt-3 text-sm text-red-500">{loginSnapshot.error}</p></div> : <div><span className="mx-auto block h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" /><p className="mt-4 text-xs text-slate-400">正在生成登录二维码…</p></div>}</div></Modal>}

      {setup && <Modal title="保存连接信息" description="敏感 Token 只在创建完成时展示，请立即保存。" onClose={() => setSetup(null)}><div className="space-y-3 px-6 py-5">{Object.entries(setup).map(([key, value]) => <div key={key} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3"><div className="text-[10px] uppercase tracking-wider text-slate-400">{key}</div><div className="mt-1 flex items-center justify-between gap-3"><code className="break-all font-mono text-xs text-slate-700">{String(value)}</code><button type="button" onClick={() => void navigator.clipboard.writeText(String(value))} className="shrink-0 text-slate-400 hover:text-[#409eff]"><Icon name="copy" className="h-4 w-4" /></button></div></div>)}</div></Modal>}

      {health && <Modal title={`运行状态 · ${health.connection.name}`} onClose={() => setHealth(null)}><div className="px-6 py-5"><div className="mb-4 flex items-center justify-between"><span className="text-xs text-slate-500">实时健康检查结果</span><StatusBadge status={health.snapshot.status} /></div><pre className="max-h-72 overflow-auto rounded-md bg-slate-900 p-4 font-mono text-[11px] leading-5 text-slate-200">{JSON.stringify(health.snapshot, null, 2)}</pre></div></Modal>}
    </div>
  );
}

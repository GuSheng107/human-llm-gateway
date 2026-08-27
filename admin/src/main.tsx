import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type View = "tasks" | "keys" | "connectors" | "llm" | "audit";
type Task = { id: string; api_key_id: number; protocol: string; model: string; status: string; error?: string; created_at: string };
type Key = { id: number; name: string; prefix: string; active: boolean; operator_name: string; im_name: string; platform: string; route_mode: string; model_name: string };
type Detail = Task & { events: { sequence: number; kind: string; content: string; tool_name?: string; tool_args?: string; source: string }[] };
type Provider = { id: number; name: string; protocol: string; base_url: string; active: boolean };
type Route = { id: number; name: string; model_name: string; upstream_model: string; mode: string; provider_id?: number; human_timeout_seconds: number };
type Connection = { id: number; name: string; platform: string; status: string };
type Health = { status: string; platform?: string; login_state?: string; qr?: string; error?: string; bot_id?: string; connected?: number; mode?: string; inbound_url?: string };

const PLATFORMS: [string, string][] = [
  ["wechat_ilink", "个人微信 iLink"], ["wecom", "企业微信(应用)"], ["webhook", "自定义 Webhook"],
  ["websocket", "WebSocket 通道"], ["http", "HTTP 轮询通道"], ["telegram", "Telegram Bot"],
  ["fake", "Fake(本地演示)"], ["wechat_sidecar", "个人微信 Sidecar(占位)"],
];

const PLATFORM_FIELDS: Record<string, [string, string][]> = {
  wechat_ilink: [["chat_id", "运营者微信 ID(发首条消息后自动绑定)"]],
  wecom: [["corp_id", "企业 ID"], ["corp_secret", "应用 Secret"], ["agent_id", "应用 AgentId"],
          ["token", "回调 Token"], ["encoding_aes_key", "回调 EncodingAESKey"], ["chat_id", "运营者 userid"]],
  webhook: [["inbound_token", "进站 Token(放 X-Connector-Token 头)"], ["target_url", "出站 Webhook URL"]],
  websocket: [["auth_token", "连接鉴权 Token"]],
  http: [["inbound_url", "轮询 URL"], ["target_url", "出站 URL"], ["poll_interval_seconds", "轮询间隔(秒)"]],
  telegram: [["bot_token", "Bot Token"], ["chat_id", "会话 ID"]],
  fake: [], wechat_sidecar: [],
};

const API = import.meta.env.DEV ? "/api" : "";
const statusLabels: Record<string, string> = { human_waiting: "等待人工", pseudo_streaming: "正在输出", completed: "已完成", timeout: "已超时", failed: "失败", cancelled: "已取消", llm_streaming: "LLM 处理中" };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("hlg_admin_token");
  const response = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers ?? {}) } });
  if (!response.ok) { const body = await response.json().catch(() => ({ detail: response.statusText })); throw new Error(typeof body.detail === "string" ? body.detail : "请求失败"); }
  return response.json();
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("admin"); const [password, setPassword] = useState(""); const [showPassword, setShowPassword] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setError(""); try { const result = await request<{ access_token: string }>("/admin/login", { method: "POST", body: JSON.stringify({ username, password }) }); localStorage.setItem("hlg_admin_token", result.access_token); onLogin(); } catch (err) { setError(err instanceof Error ? err.message : "登录失败"); } };
  return <main className="login-shell"><section className="login-card"><div className="eyebrow">HUMAN LLM GATEWAY / 01</div><h1>Control<br /><em>Room</em></h1><p>把一段真人回复，精确地送回 API 请求。</p><form onSubmit={submit}><label>管理员账号<input value={username} onChange={(e) => setUsername(e.target.value)} /></label><label>密码<div className="password-field"><input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoFocus /><button type="button" className="password-toggle" aria-label={showPassword ? "隐藏密码" : "显示密码"} title={showPassword ? "隐藏密码" : "显示密码"} onClick={() => setShowPassword((visible) => !visible)}>{showPassword ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A11.7 11.7 0 0 1 12 5c5.2 0 8.7 4.2 10 7-0.5 1.1-1.5 2.8-3.1 4.3M6.1 6.1C4.2 7.4 2.9 9.5 2 12c1.3 2.8 4.8 7 10 7 1.3 0 2.5-.2 3.6-.7" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="2.5" /></svg>}</button></div></label>{error && <div className="error">{error}</div>}<button className="primary" type="submit">进入控制室 <span>↗</span></button></form></section><div className="login-mark">HLG<span>·</span>OPS</div></main>;
}

function ConfigView({ view, providers, routes, connections, onRefresh, notify }: { view: View; providers: Provider[]; routes: Route[]; connections: Connection[]; onRefresh: () => void; notify: (message: string) => void }) {
  const [provider, setProvider] = useState({ name: "", protocol: "openai_compatible", base_url: "", api_key: "" }); const [route, setRoute] = useState({ name: "", model_name: "", upstream_model: "", mode: "human", provider_id: "", human_timeout_seconds: "300" }); const [connection, setConnection] = useState({ name: "", platform: "telegram", config: "{}" }); const [catalog, setCatalog] = useState<Record<number, string[]>>({});
  const saveProvider = async (event: FormEvent) => { event.preventDefault(); try { await request("/admin/providers", { method: "POST", body: JSON.stringify(provider) }); setProvider({ name: "", protocol: "openai_compatible", base_url: "", api_key: "" }); notify("LLM 供应商已保存"); onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : "保存失败"); } };
  const syncProvider = async (id: number) => { try { const result = await request<{ data: { id: string }[] }>(`/admin/providers/${id}/models/sync`, { method: "POST" }); setCatalog((current) => ({ ...current, [id]: result.data.map((item) => item.id) })); notify(`已获取 ${result.data.length} 个模型`); onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : "获取模型失败"); } };
  const saveRoute = async (event: FormEvent) => { event.preventDefault(); try { await request("/admin/routes", { method: "POST", body: JSON.stringify({ ...route, provider_id: route.provider_id ? Number(route.provider_id) : null, human_timeout_seconds: Number(route.human_timeout_seconds) }) }); setRoute({ name: "", model_name: "", upstream_model: "", mode: "human", provider_id: "", human_timeout_seconds: "300" }); notify("模型路由已保存"); onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : "保存失败"); } };
  const saveConnection = async (event: FormEvent) => { event.preventDefault(); try { await request("/admin/connectors", { method: "POST", body: JSON.stringify({ name: connection.name, platform: connection.platform, config: JSON.parse(connection.config) }) }); setConnection({ name: "", platform: "telegram", config: "{}" }); notify("IM 连接已保存"); onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : "配置 JSON 无效或保存失败"); } };
  if (view === "audit") return <section className="config-page panel detail-empty"><div className="crosshair">＋</div><h3>审计日志</h3><p>审计记录已在后端统一写入；筛选和导出将在下一迭代开放。</p></section>;
  if (view === "connectors") return <ConnectorsView connections={connections} onRefresh={onRefresh} notify={notify} />;
  return <section className="config-page panel"><div className="panel-head"><div><span className="section-kicker">PROVIDERS / ROUTING FABRIC</span><h3>LLM 供应商与模型路由</h3></div><span className="live-pill">● NO LIVE PROBE</span></div><div className="config-columns"><form className="config-form" onSubmit={saveProvider}><h4>新增供应商</h4><label>名称<input required value={provider.name} onChange={(e) => setProvider({ ...provider, name: e.target.value })} /></label><label>协议<select value={provider.protocol} onChange={(e) => setProvider({ ...provider, protocol: e.target.value })}><option value="openai_compatible">OpenAI Compatible</option><option value="anthropic">Anthropic Messages</option></select></label><label>Base URL<input required placeholder="https://.../v1 或 mock://local" value={provider.base_url} onChange={(e) => setProvider({ ...provider, base_url: e.target.value })} /></label><label>API Key（只写入密文）<input type="password" value={provider.api_key} onChange={(e) => setProvider({ ...provider, api_key: e.target.value })} /></label><button className="primary small" type="submit">保存供应商</button></form><form className="config-form" onSubmit={saveRoute}><h4>新增路由</h4><label>路由名称<input required value={route.name} onChange={(e) => setRoute({ ...route, name: e.target.value })} /></label><label>对外模型名<input required placeholder="客户端可见名称" value={route.model_name} onChange={(e) => setRoute({ ...route, model_name: e.target.value })} /></label><label>实际上游模型<input required list="model-catalog" placeholder="由管理员决定，客户端参数不覆盖" value={route.upstream_model} onChange={(e) => setRoute({ ...route, upstream_model: e.target.value })} /></label><label>处理模式<select value={route.mode} onChange={(e) => setRoute({ ...route, mode: e.target.value })}><option value="human">人工</option><option value="llm">真实 LLM</option><option value="human_fallback_llm">人工超时后 LLM</option></select></label><label>供应商<select value={route.provider_id} onChange={(e) => setRoute({ ...route, provider_id: e.target.value })}><option value="">不绑定</option>{providers.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><button className="primary small" type="submit">保存路由</button></form></div><datalist id="model-catalog">{Object.values(catalog).flat().map((model) => <option value={model} key={model} />)}</datalist><div className="config-list"><h4>已配置供应商</h4>{providers.map((item) => <div className="config-row" key={item.id}><strong>{item.name}</strong><span>{item.protocol}</span><span>{item.base_url}</span><span>{catalog[item.id]?.length ? `${catalog[item.id].length} models` : "未同步"}</span><button className="ghost small" onClick={() => void syncProvider(item.id)}>同步模型</button></div>)}</div><div className="config-list"><h4>已配置路由</h4>{routes.map((item) => <div className="config-row" key={item.id}><strong>{item.name}</strong><span>{item.model_name} → {item.upstream_model}</span><span>{item.mode}</span><span>timeout {item.human_timeout_seconds}s</span></div>)}</div></section>;
}

function IlinkLoginModal({ connectionId, onClose, onDone }: { connectionId: number; onClose: () => void; onDone: () => void }) {
  const [snap, setSnap] = useState<Health>({ status: "", login_state: "pending" });
  const start = async () => {
    try { setSnap(await request<Health>(`/admin/connectors/${connectionId}/login`, { method: "POST" })); }
    catch (err) { setSnap({ status: "", login_state: "error", error: err instanceof Error ? err.message : "启动失败" }); }
  };
  useEffect(() => {
    void start();
    const timer = setInterval(async () => {
      try {
        const next = await request<Health>(`/admin/connectors/${connectionId}/login`);
        setSnap(next);
        if (next.login_state === "connected") { clearInterval(timer); onDone(); }
      } catch { /* ignore poll errors */ }
    }, 2000);
    return () => clearInterval(timer);
  }, [connectionId]);
  return <div className="modal-backdrop"><div className="modal">
    <div className="section-kicker">WECHAT / ILINK LOGIN</div><h3>扫码登录个人微信</h3>
    <div style={{ display: "grid", placeItems: "center", minHeight: 260 }}>
      {snap.login_state === "connected" ? <p>已连接 · Bot {snap.bot_id}</p>
        : snap.qr ? <img src={snap.qr} alt="登录二维码" style={{ width: 260, height: 260 }} />
        : <p>{snap.login_state === "scanned" ? "已扫码,请在手机确认…" : "正在获取二维码…"}</p>}
    </div>
    {snap.error && <div className="error">{snap.error}</div>}
    <div className="modal-actions"><button className="ghost" onClick={onClose}>关闭</button></div>
  </div></div>;
}

function ConnectorsView({ connections, onRefresh, notify }: { connections: Connection[]; onRefresh: () => void; notify: (message: string) => void }) {
  const [form, setForm] = useState({ name: "", platform: "wechat_ilink" });
  const [fields, setFields] = useState<Record<string, string>>({});
  const [healths, setHealths] = useState<Record<number, Health>>({});
  const [loginId, setLoginId] = useState<number | null>(null);
  const checkHealth = async (id: number) => {
    try { const data = await request<Health>(`/admin/connectors/${id}/health`); setHealths((h) => ({ ...h, [id]: data })); }
    catch (err) { notify(err instanceof Error ? err.message : "健康检查失败"); }
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await request("/admin/connectors", { method: "POST", body: JSON.stringify({ name: form.name, platform: form.platform, config: { ...fields, chat_id: fields.chat_id || "" } }) });
      notify("连接已保存");
      setForm({ name: "", platform: form.platform });
      setFields({});
      onRefresh();
    } catch (err) { notify(err instanceof Error ? err.message : "保存失败"); }
  };
  const start = async (id: number) => { try { await request(`/admin/connectors/${id}/start`, { method: "POST" }); notify("连接器已启动"); await checkHealth(id); } catch (err) { notify(err instanceof Error ? err.message : "启动失败"); } };
  const stop = async (id: number) => { try { await request(`/admin/connectors/${id}/stop`, { method: "POST" }); notify("连接器已停止"); onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : "停止失败"); } };
  return <section className="config-page panel">
    <div className="panel-head"><div><span className="section-kicker">CHANNELS / GATEWAY CONNECTORS</span><h3>IM 连接器</h3></div><span className="live-pill">● CONFIG + LIVE</span></div>
    <form className="config-form" onSubmit={save}>
      <h4>新增连接</h4>
      <label>连接名称<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
      <label>平台<select value={form.platform} onChange={(e) => { setForm({ ...form, platform: e.target.value }); setFields({}); }}>{PLATFORMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      {(PLATFORM_FIELDS[form.platform] ?? []).map(([key, label]) => <label key={key}>{label}<input type={/(token|secret|aes_key)/i.test(key) ? "password" : "text"} value={fields[key] ?? ""} onChange={(e) => setFields({ ...fields, [key]: e.target.value })} /></label>)}
      <button className="primary small" type="submit">保存连接</button>
    </form>
    <div className="config-list"><h4>已配置连接</h4>
      {connections.map((item) => <div className="config-row" key={item.id}>
        <strong>{item.name}</strong><span>{item.platform}</span>
        <span className={`status status-${healths[item.id]?.status ?? item.status}`}>{healths[item.id]?.status ?? item.status}{healths[item.id]?.connected ? ` (${healths[item.id].connected} 在线)` : ""}</span>
        {healths[item.id]?.login_state && <span>登录: {healths[item.id].login_state}</span>}
        <button className="ghost small" onClick={() => void checkHealth(item.id)}>健康</button>
        <button className="ghost small" onClick={() => void start(item.id)}>启动</button>
        <button className="ghost small" onClick={() => void stop(item.id)}>停止</button>
        {item.platform === "wechat_ilink" && <button className="primary small" onClick={() => setLoginId(item.id)}>扫码登录</button>}
        {item.platform === "webhook" && <small>进站: POST /connectors/webhook/{item.id}/inbound</small>}
        {item.platform === "websocket" && <small>WS: /connectors/ws/{item.id}?token=…</small>}
      </div>)}
    </div>
    {loginId && <IlinkLoginModal connectionId={loginId} onClose={() => setLoginId(null)} onDone={() => { setLoginId(null); void onRefresh(); }} />}
  </section>;
}

function App() {
  const [authed, setAuthed] = useState(Boolean(localStorage.getItem("hlg_admin_token"))); const [view, setView] = useState<View>("tasks"); const [tasks, setTasks] = useState<Task[]>([]); const [keys, setKeys] = useState<Key[]>([]); const [providers, setProviders] = useState<Provider[]>([]); const [routes, setRoutes] = useState<Route[]>([]); const [connections, setConnections] = useState<Connection[]>([]); const [selected, setSelected] = useState<Detail | null>(null); const [reply, setReply] = useState("/think\n\n/reply\n\n/done"); const [toast, setToast] = useState(""); const [newKeyOpen, setNewKeyOpen] = useState(false); const [keySecret, setKeySecret] = useState(""); const [form, setForm] = useState({ name: "", operator_name: "", im_name: "", platform: "fake", model_name: "human-default", route_id: "", im_connection_id: "" });
  const refresh = async () => { try { const [nextTasks, nextKeys, nextProviders, nextRoutes, nextConnections] = await Promise.all([request<Task[]>("/admin/tasks"), request<Key[]>("/admin/api-keys"), request<Provider[]>("/admin/providers"), request<Route[]>("/admin/routes"), request<Connection[]>("/admin/connectors")]); setTasks(nextTasks); setKeys(nextKeys); setProviders(nextProviders); setRoutes(nextRoutes); setConnections(nextConnections); } catch (err) { setToast(err instanceof Error ? err.message : "刷新失败"); } };
  useEffect(() => { if (authed) void refresh(); }, [authed]); const stats = useMemo(() => ({ waiting: tasks.filter((t) => t.status === "human_waiting").length, done: tasks.filter((t) => t.status === "completed").length, keys: keys.length }), [tasks, keys]);
  const openTask = async (task: Task) => { try { setSelected(await request<Detail>(`/admin/tasks/${task.id}`)); } catch (err) { setToast(err instanceof Error ? err.message : "任务读取失败"); } }; const sendReply = async () => { if (!selected) return; try { await request(`/admin/tasks/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ text: reply }) }); setToast("网页回复已接收，正在生成伪流式轨迹"); await refresh(); await openTask(selected); } catch (err) { setToast(err instanceof Error ? err.message : "回复失败"); } }; const createKey = async (event: FormEvent) => { event.preventDefault(); try { const created = await request<Key & { secret: string }>("/admin/api-keys", { method: "POST", body: JSON.stringify({ ...form, route_id: form.route_id ? Number(form.route_id) : null, im_connection_id: form.im_connection_id ? Number(form.im_connection_id) : null }) }); setKeySecret(created.secret); setNewKeyOpen(false); await refresh(); } catch (err) { setToast(err instanceof Error ? err.message : "创建失败"); } };
  if (!authed) return <Login onLogin={() => setAuthed(true)} />; const nav: [View, string, string][] = [["tasks", "◈", "任务台"], ["keys", "⌁", "API Keys"], ["connectors", "⊙", "连接器"], ["llm", "◌", "LLM 路由"], ["audit", "▤", "审计日志"]];
  return <div className="app-shell"><aside><div className="brand"><span className="brand-dot" /> HLG <small>OPS CONSOLE</small></div><nav>{nav.map(([id, icon, label]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}><span>{icon}</span>{label}</button>)}</nav><div className="side-foot"><div className="live-dot" /> CORE ONLINE<br /><span>SQLite · local mode</span></div></aside><main className="content"><header><div><div className="eyebrow">OPERATIONS / {view === "tasks" ? "LIVE QUEUE" : view.toUpperCase()}</div><h2>{nav.find(([id]) => id === view)?.[2]} <span>·</span></h2></div><div className="header-actions"><button onClick={() => void refresh()} className="ghost">↻ 刷新</button><button className="primary small" onClick={() => setNewKeyOpen(true)}>+ 新建 API Key</button></div></header>{view === "tasks" ? <><section className="stats"><div><small>待人工回复</small><strong>{stats.waiting.toString().padStart(2, "0")}</strong><i>LIVE QUEUE</i></div><div><small>已完成任务</small><strong>{stats.done.toString().padStart(2, "0")}</strong><i>ALL TIME</i></div><div><small>绑定 Key</small><strong>{stats.keys.toString().padStart(2, "0")}</strong><i>ISOLATED ROUTES</i></div><div className="signal"><small>系统状态</small><strong><span className="live-dot" />运行中</strong><i>NO EXTERNAL CALLS</i></div></section><section className="workspace"><div className="queue panel"><div className="panel-head"><div><span className="section-kicker">INBOX / {tasks.length}</span><h3>人工队列</h3></div><span className="live-pill">● LIVE</span></div>{tasks.length === 0 ? <div className="empty"><div>∅</div><p>还没有请求进入队列</p><small>使用 API Key 发起一次对话后，任务会出现在这里。</small></div> : <div className="task-list">{tasks.map((task) => <button key={task.id} className={`task-row ${selected?.id === task.id ? "selected" : ""}`} onClick={() => void openTask(task)}><div className="task-icon">{task.protocol === "anthropic" ? "A" : "O"}</div><div className="task-main"><strong>{task.model}</strong><span>{task.id.slice(0, 8)} · Key #{task.api_key_id}</span></div><span className={`status status-${task.status}`}>{statusLabels[task.status] ?? task.status}</span><time>{new Date(task.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></button>)}</div>}</div><div className="detail panel">{selected ? <><div className="panel-head"><div><span className="section-kicker">TASK / {selected.id.slice(0, 12)}</span><h3>网页人工回复</h3></div><span className={`status status-${selected.status}`}>{statusLabels[selected.status] ?? selected.status}</span></div><div className="timeline">{selected.events.length === 0 ? <div className="timeline-empty">等待真人或网页回复……</div> : selected.events.map((event) => <div className="event" key={event.sequence}><span className="event-kind">{event.kind}</span><p>{event.content || `${event.tool_name} ${event.tool_args}`}</p><small>source: {event.source}</small></div>)}</div><div className="reply-box"><div className="reply-label"><span>MANUAL RESPONSE / WEB</span><small>/think · /tool · /reply · /done</small></div><textarea value={reply} onChange={(e) => setReply(e.target.value)} disabled={selected.status !== "human_waiting"} /><button className="primary reply-btn" onClick={() => void sendReply()} disabled={selected.status !== "human_waiting"}>发送网页人工回复 <span>↗</span></button></div></> : <div className="detail-empty"><div className="crosshair">＋</div><h3>选择一个任务</h3><p>在左侧队列中查看上下文，并从网页直接接管回复。</p></div>}</div></section></> : <ConfigView view={view} providers={providers} routes={routes} connections={connections} onRefresh={() => void refresh()} notify={setToast} />}{view === "keys" && <section className="panel key-strip">{keys.map((item) => <div className="config-row" key={item.id}><strong>{item.name}</strong><span>{item.operator_name}</span><span>{item.im_name} · {item.platform}</span><span>{item.model_name}</span><span>{item.prefix}…</span></div>)}</section>}{toast && <button className="toast" onClick={() => setToast("")}>{toast} ×</button>}</main>{newKeyOpen && <div className="modal-backdrop"><form className="modal" onSubmit={createKey}><div className="section-kicker">NEW ISOLATED ROUTE</div><h3>创建 API Key 绑定</h3><p>一组 Key 只绑定一个真人和一个 IM 连接。</p>{(["name", "operator_name", "im_name"] as const).map((field) => <label key={field}>{field === "name" ? "Key 名称" : field === "operator_name" ? "真人昵称" : "IM 连接名称"}<input required value={form[field]} onChange={(e) => setForm({ ...form, [field]: e.target.value })} /></label>)}<label>平台<select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}><option value="fake">Fake（本地演示）</option><option value="telegram">Telegram</option><option value="wecom">企业微信</option><option value="wechat_sidecar">个人微信 Sidecar</option><option value="wechat_ilink">个人微信 iLink</option><option value="webhook">自定义 Webhook</option><option value="websocket">WebSocket 通道</option><option value="http">HTTP 轮询通道</option></select></label><label>绑定 IM 连接<select value={form.im_connection_id} onChange={(e) => setForm({ ...form, im_connection_id: e.target.value })}><option value="">新建连接</option>{connections.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.platform}</option>)}</select></label><label>绑定模型路由<select value={form.route_id} onChange={(e) => setForm({ ...form, route_id: e.target.value })}><option value="">新建默认路由</option>{routes.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.model_name} → {item.upstream_model}</option>)}</select></label><div className="modal-actions"><button type="button" className="ghost" onClick={() => setNewKeyOpen(false)}>取消</button><button className="primary" type="submit">创建并显示 Key</button></div></form></div>}{keySecret && <div className="modal-backdrop"><div className="modal secret-modal"><div className="section-kicker">COPY ONCE / API KEY</div><h3>密钥只显示这一次</h3><p>请立即复制并安全保存，数据库只保存 hash。</p><code>{keySecret}</code><button className="primary" onClick={() => { void navigator.clipboard?.writeText(keySecret); setKeySecret(""); setToast("已复制到剪贴板"); }}>复制并关闭</button></div></div>}</div>;
}

createRoot(document.getElementById("root")!).render(<App />);

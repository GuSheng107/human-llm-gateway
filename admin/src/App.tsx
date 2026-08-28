import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, login, TOKEN_KEY } from "./api";
import { AppShell } from "./components/AppShell";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { Icon } from "./icons";
import type { CurrentUser, ViewId } from "./types";

function LoginPage({ onLogin }: { onLogin: (user: CurrentUser) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await login(username.trim(), password);
      onLogin(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="grid min-h-screen bg-[#f4f6f9] lg:grid-cols-[minmax(420px,46%)_1fr]">
      <section className="hidden bg-[#263445] px-12 py-14 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md bg-[#409eff] font-mono text-base font-bold">H</div>
          <div>
            <div className="text-sm font-semibold">Human LLM Gateway</div>
            <div className="mt-1 text-[10px] uppercase tracking-[.18em] text-slate-400">operator console</div>
          </div>
        </div>
        <div className="max-w-md">
          <div className="mb-5 h-1 w-10 rounded-full bg-[#409eff]" />
          <h1 className="text-3xl font-semibold leading-tight">真人驱动的模型兼容网关</h1>
          <p className="mt-4 text-sm leading-7 text-slate-400">在自己的 IM Bot 中完成身份绑定，并把完整回复转换为兼容的模型响应。</p>
        </div>
        <div className="text-[11px] text-slate-500">Human Gateway Administration</div>
      </section>

      <section className="flex items-center justify-center px-5 py-12">
        <div className="w-full max-w-[380px] rounded-lg border border-slate-200 bg-white p-8 shadow-[0_12px_40px_rgba(15,23,42,.08)]">
          <div className="mb-7 lg:hidden">
            <div className="inline-grid h-10 w-10 place-items-center rounded-md bg-[#409eff] font-mono font-bold text-white">H</div>
          </div>
          <h2 className="text-xl font-semibold text-slate-800">登录管理台</h2>
          <p className="mt-2 text-xs text-slate-400">管理员与普通用户使用各自账号登录</p>
          <form onSubmit={submit} className="mt-7 space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">账号</span>
              <input autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">密码</span>
              <input autoComplete="current-password" required type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="field-input" />
            </label>
            {error && <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-500">{error}</div>}
            <button disabled={submitting} className="flex h-10 w-full items-center justify-center rounded-md bg-[#409eff] text-sm font-medium text-white transition hover:bg-[#337ecc] disabled:cursor-not-allowed disabled:opacity-60">
              {submitting ? "正在登录…" : "登录"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}

function Placeholder({ view }: { view: ViewId }) {
  const labels: Record<ViewId, string> = {
    console: "控制台",
    connections: "连接 IM",
    api: "API 管理",
    llm: "LLM 管理",
    reply: "网页回复端",
    settings: "基础设置",
    users: "用户管理",
  };
  return (
    <section className="grid min-h-[360px] place-items-center rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="text-center">
        <div className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-slate-50 text-slate-300">
          <Icon name="settings" className="h-5 w-5" />
        </div>
        <h1 className="mt-4 text-sm font-medium text-slate-700">{labels[view]}</h1>
        <p className="mt-1.5 text-xs text-slate-400">暂未开放</p>
      </div>
    </section>
  );
}

export default function App() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));
  const [view, setView] = useState<ViewId>("console");
  const [refreshKey, setRefreshKey] = useState(0);
  const [notice, setNotice] = useState("");

  const notify = useCallback((message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice((current) => current === message ? "" : current), 3200);
  }, []);

  useEffect(() => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      setChecking(false);
      return;
    }
    api<CurrentUser>("/auth/me")
      .then(setUser)
      .catch(() => localStorage.removeItem(TOKEN_KEY))
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return <main className="grid min-h-screen place-items-center bg-[#f4f6f9]"><span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" /></main>;
  }
  if (!user) return <LoginPage onLogin={setUser} />;

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setUser(null);
  };

  return (
    <>
      <AppShell user={user} view={view} onView={setView} onLogout={logout} onRefresh={() => setRefreshKey((value) => value + 1)}>
        {view === "connections" ? <ConnectionsPage user={user} refreshKey={refreshKey} notify={notify} /> : <Placeholder view={view} />}
      </AppShell>
      {notice && <div role="status" className="fixed bottom-5 right-5 z-[70] max-w-sm rounded-md bg-slate-800 px-4 py-3 text-xs text-white shadow-xl">{notice}</div>}
    </>
  );
}

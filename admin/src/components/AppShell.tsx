import type { ReactNode } from "react";
import { Icon } from "../icons";
import type { CurrentUser, ViewId } from "../types";

interface MenuItem {
  id: ViewId;
  label: string;
  icon: string;
}

const groups: { label: string; items: MenuItem[] }[] = [
  { label: "工作台", items: [{ id: "console", label: "控制台", icon: "dashboard" }] },
  {
    label: "接入管理",
    items: [
      { id: "connections", label: "连接 IM", icon: "link" },
      { id: "api", label: "API 管理", icon: "key" },
      { id: "llm", label: "LLM 管理", icon: "cpu" },
    ],
  },
  { label: "人工工作区", items: [{ id: "reply", label: "网页回复端", icon: "reply" }] },
  {
    label: "系统设置",
    items: [
      { id: "settings", label: "基础设置", icon: "settings" },
      { id: "users", label: "用户管理", icon: "users" },
    ],
  },
];

const titles: Record<ViewId, { title: string; description: string }> = {
  console: { title: "控制台", description: "运行概览与关键指标" },
  connections: { title: "连接 IM", description: "管理用户 Bot、平台身份绑定与运行状态" },
  api: { title: "API 管理", description: "API Key 与模型路由" },
  llm: { title: "LLM 管理", description: "真实模型供应商与降级配置" },
  reply: { title: "网页回复端", description: "在网页中伪造 Agent 完整回复" },
  settings: { title: "基础设置", description: "系统运行参数" },
  users: { title: "用户管理", description: "账号、角色与状态" },
};

interface Props {
  user: CurrentUser;
  view: ViewId;
  onView: (view: ViewId) => void;
  onLogout: () => void;
  onRefresh: () => void;
  children: ReactNode;
}

export function AppShell({ user, view, onView, onLogout, onRefresh, children }: Props) {
  const current = titles[view];
  return (
    <div className="min-h-screen bg-[#f4f6f9] text-slate-700 lg:grid lg:grid-cols-[224px_minmax(0,1fr)]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 flex-col bg-[#263445] text-slate-300 shadow-xl lg:flex">
        <div className="flex h-16 items-center gap-3 border-b border-white/10 px-5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[#409eff] font-mono text-sm font-bold text-white shadow-[0_6px_18px_rgba(64,158,255,.3)]">H</div>
          <div>
            <div className="text-sm font-semibold tracking-wide text-white">Human Gateway</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[.18em] text-slate-400">operator console</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {groups.map((group) => (
            <section key={group.label} className="mb-5">
              <h2 className="mb-2 px-3 text-[10px] font-medium uppercase tracking-[.16em] text-slate-500">{group.label}</h2>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = item.id === view;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => onView(item.id)}
                      className={`group flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-[13px] transition ${active ? "bg-[#409eff] font-medium text-white shadow-sm" : "text-slate-300 hover:bg-white/10 hover:text-white"}`}
                    >
                      <Icon name={item.icon} className={`h-[17px] w-[17px] ${active ? "text-white" : "text-slate-400 group-hover:text-white"}`} />
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </nav>

        <div className="border-t border-white/10 p-3">
          <div className="flex items-center gap-3 rounded-lg bg-black/10 px-3 py-3">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#409eff]/20 text-xs font-semibold text-[#79bbff]">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-white">{user.display_name || user.username}</div>
              <div className="mt-0.5 text-[10px] text-slate-500">{user.role === "admin" ? "系统管理员" : "Bot 操作者"}</div>
            </div>
            <button type="button" onClick={onLogout} className="rounded p-1.5 text-slate-500 transition hover:bg-white/10 hover:text-white" title="退出登录">
              <Icon name="logout" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="min-w-0 lg:col-start-2">
        <header className="sticky top-0 z-20 flex min-h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur lg:px-7">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <span className="text-slate-400">Human Gateway</span>
              <span className="text-slate-300">/</span>
              <span>{current.title}</span>
            </div>
            <p className="mt-0.5 hidden text-[11px] text-slate-400 sm:block">{current.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <select value={view} onChange={(event) => onView(event.target.value as ViewId)} className="h-9 max-w-32 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-600 outline-none lg:hidden">
              {groups.flatMap((group) => group.items).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
            <button type="button" onClick={onRefresh} className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 shadow-sm transition hover:border-[#a0cfff] hover:text-[#409eff]">
              <Icon name="refresh" className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">刷新</span>
            </button>
            <button type="button" onClick={onLogout} className="grid h-9 w-9 place-items-center rounded-md border border-slate-200 bg-white text-slate-500 lg:hidden" title="退出登录"><Icon name="logout" className="h-4 w-4" /></button>
          </div>
        </header>
        <main className="p-4 sm:p-6 lg:p-7">{children}</main>
      </div>
    </div>
  );
}

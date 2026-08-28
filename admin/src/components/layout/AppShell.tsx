import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Icon } from "../../icons";
import { useAuth } from "../../features/auth/AuthContext";

interface MenuItem {
  to: string;
  label: string;
  icon: string;
}

const NAV: { label: string; items: MenuItem[] }[] = [
  { label: "工作台", items: [{ to: "/", label: "控制台", icon: "dashboard" }] },
  { label: "账号", items: [{ to: "/account", label: "账号设置", icon: "settings" }] },
];

function pageDescription(pathname: string): string {
  if (pathname.startsWith("/account")) return "个人资料与安全";
  return "运行概览与关键指标";
}

export function AppShell() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  if (!user) return null;
  const navBody = (
    <nav className="flex-1 overflow-y-auto px-3 py-4">
      {NAV.map((group) => (
        <section key={group.label} className="mb-5">
          <h2 className="mb-2 px-3 text-[10px] font-medium uppercase tracking-[.16em] text-slate-500">{group.label}</h2>
          <div className="space-y-1">
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `group flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-[13px] transition ${
                    isActive
                      ? "bg-[#409eff] font-medium text-white shadow-sm"
                      : "text-slate-300 hover:bg-white/10 hover:text-white"
                  }`
                }
              >
                <Icon name={item.icon} className="h-[17px] w-[17px]" />
                {item.label}
              </NavLink>
            ))}
          </div>
        </section>
      ))}
    </nav>
  );

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
        {navBody}
        <div className="border-t border-white/10 p-3">
          <div className="flex items-center gap-3 rounded-lg bg-black/10 px-3 py-3">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#409eff]/20 text-xs font-semibold text-[#79bbff]">
              {(user.display_name || user.username).slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-white">{user.display_name || user.username}</div>
              <div className="mt-0.5 text-[10px] text-slate-500">{user.role === "admin" ? "系统管理员" : "普通用户"}</div>
            </div>
            <button
              type="button"
              onClick={logout}
              className="rounded p-1.5 text-slate-500 transition hover:bg-white/10 hover:text-white"
              title="退出登录"
            >
              <Icon name="logout" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" onMouseDown={(event) => event.target === event.currentTarget && setMobileOpen(false)}>
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-[#263445] text-slate-300 shadow-2xl">
            <div className="flex h-14 items-center justify-between border-b border-white/10 px-4">
              <span className="text-sm font-semibold text-white">Human Gateway</span>
              <button type="button" onClick={() => setMobileOpen(false)} className="rounded p-1.5 text-slate-400 hover:text-white" aria-label="关闭导航">
                <Icon name="close" className="h-4 w-4" />
              </button>
            </div>
            {navBody}
          </aside>
        </div>
      )}

      <div className="min-w-0 lg:col-start-2">
        <header className="sticky top-0 z-20 flex min-h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur lg:px-7">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                className="grid h-8 w-8 place-items-center rounded-md border border-slate-200 text-slate-500 lg:hidden"
                aria-label="打开导航"
              >
                <Icon name="list" className="h-4 w-4" />
              </button>
              <span className="text-slate-400">Human Gateway</span>
              <span className="text-slate-300">/</span>
              <span>{NAV.flatMap((g) => g.items).find((item) =>
                item.to === location.pathname ||
                (item.to !== "/" && location.pathname.startsWith(item.to)),
              )?.label ?? "控制台"}</span>
            </div>
            <p className="mt-0.5 hidden text-[11px] text-slate-400 sm:block">{pageDescription(location.pathname)}</p>
          </div>
          <button
            type="button"
            onClick={logout}
            className="grid h-9 w-9 place-items-center rounded-md border border-slate-200 bg-white text-slate-500 lg:hidden"
            title="退出登录"
          >
            <Icon name="logout" className="h-4 w-4" />
          </button>
        </header>
        <main className="p-4 sm:p-6 lg:p-7">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../features/auth/AuthContext";
import { BrandLogo } from "../brand/Brand";
import { Icon } from "../../icons";
import { canAccess, matchNavigation, NAVIGATION } from "../../navigation";

export function AppShell() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  if (!user) return null;
  const visibleNav = NAVIGATION.map((group) => ({
    ...group,
    items: group.items.filter((item) => canAccess(user.capabilities, item)),
  })).filter((group) => group.items.length > 0);
  const currentRoute = matchNavigation(location.pathname);
  const navBody = (
    <nav className="flex-1 overflow-y-auto px-3 py-4">
      {visibleNav.map((group) => (
        <section key={group.label} className="mb-5">
          <h2 className="mb-2 px-3 text-micro font-medium uppercase tracking-widest text-slate-500">
            {group.label}
          </h2>
          <div className="space-y-1">
            {group.items.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `group flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm transition ${
                    isActive
                      ? "bg-primary font-medium text-white shadow-card"
                      : "text-slate-300 hover:bg-white/10 hover:text-white"
                  }`
                }
              >
                <Icon name={item.icon} className="h-4 w-4" />
                {item.label}
              </NavLink>
            ))}
          </div>
        </section>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen bg-page text-slate-700 lg:grid lg:grid-cols-[224px_minmax(0,1fr)]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 flex-col bg-sidebar text-slate-300 shadow-modal lg:flex">
        <div className="flex h-16 items-center gap-3 border-b border-white/10 px-5">
          <BrandLogo size="sm" />
          <div className="text-sm font-semibold tracking-wide text-white">能工智人</div>
        </div>
        {navBody}
        <div className="border-t border-white/10 p-3">
          <div className="flex items-center gap-3 rounded-lg bg-black/10 px-3 py-3">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary/20 text-xs font-semibold text-primary-light">
              {(user.display_name || user.username).slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-white">
                {user.display_name || user.username}
              </div>
              <div className="mt-0.5 text-micro text-slate-500">
                {user.role === "admin" ? "系统管理员" : "普通用户"}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded p-1.5 text-slate-500 transition hover:bg-white/10 hover:text-white"
              title="退出登录"
            >
              <Icon name="logout" className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 lg:hidden"
          onMouseDown={(event) =>
            event.target === event.currentTarget && setMobileOpen(false)
          }
        >
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-sidebar text-slate-300 shadow-modal animate-slide-in-left">
            <div className="flex h-14 items-center justify-between border-b border-white/10 px-4">
              <span className="text-sm font-semibold text-white">能工智人</span>
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="rounded p-1.5 text-slate-400 hover:text-white"
                aria-label="关闭导航"
              >
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
              <span className="text-slate-400">能工智人</span>
              <span className="text-slate-300">/</span>
              <span>{currentRoute?.label ?? "无权访问此页面"}</span>
            </div>
            <p className="mt-0.5 hidden text-caption text-slate-400 sm:block">
              {currentRoute?.description ?? "无权访问此页面"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void logout()}
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

import { useAuth } from "../auth/AuthContext";

export function AccountPage() {
  const { user, logout } = useAuth();
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-6 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">账号设置</h1>
        <p className="mt-2 text-xs text-slate-400">查看账号信息与角色。</p>
        <dl className="mt-6 space-y-4 text-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <dt className="text-slate-400">账号</dt>
            <dd className="font-mono text-slate-700">{user?.username}</dd>
          </div>
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <dt className="text-slate-400">显示名</dt>
            <dd className="text-slate-700">{user?.display_name}</dd>
          </div>
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <dt className="text-slate-400">角色</dt>
            <dd className="text-slate-700">{user?.role === "admin" ? "系统管理员" : "Bot 操作者"}</dd>
          </div>
        </dl>
        <button
          type="button"
          onClick={logout}
          className="mt-6 rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 transition hover:border-red-200 hover:bg-red-50 hover:text-red-500"
        >
          退出登录
        </button>
      </section>
      <p className="text-[11px] leading-5 text-slate-400">
        修改密码功能由管理员通过用户管理入口处理；如需自助改密请联系管理员。
      </p>
    </div>
  );
}

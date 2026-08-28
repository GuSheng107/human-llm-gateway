import { useAuth } from "../auth/AuthContext";

export function DashboardPage() {
  const { user } = useAuth();
  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">
          {user?.role === "admin" ? "监管控制台" : `你好，${user?.display_name || user?.username}`}
        </h1>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          用户、邀请码与权限闭环已可用。连接 IM、LLM 配置、API Key 与任务工作台等业务模块将在后续里程碑逐步上线。
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4 text-sm font-medium text-slate-700">当前账号</div>
        <div className="divide-y divide-slate-100">
          {[
            { label: "账号", value: user?.username ?? "-" },
            { label: "显示名", value: user?.display_name ?? "-" },
            { label: "角色", value: user?.role === "admin" ? "系统管理员" : "普通用户" },
          ].map((row) => (
            <div key={row.label} className="flex items-center justify-between px-5 py-3 text-xs">
              <span className="text-slate-400">{row.label}</span>
              <span className="text-slate-700">{row.value}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

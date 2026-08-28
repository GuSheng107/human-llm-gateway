import { Card } from "../../components/data-display/Card";
import { PageHeader } from "../../components/layout/PageHeader";
import { useAuth } from "../auth/AuthContext";

export function DashboardPage() {
  const { user } = useAuth();
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <PageHeader
        title={user?.role === "admin" ? "监管控制台" : "控制台"}
        description="账号与权限概览"
      />

      <Card>
        <div className="border-b border-slate-100 px-5 py-4 text-sm font-medium text-slate-700">
          当前账号
        </div>
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
      </Card>
    </div>
  );
}

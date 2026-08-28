import { Drawer } from "../../components/feedback/Drawer";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import type { UserDetail } from "../../types/governance";

const RESOURCE_LABELS: Record<string, string> = {
  im_connections: "IM 连接",
  llm_configs: "LLM 配置",
  fake_models: "私有 Fake Model",
  model_groups: "模型分组",
  api_keys: "API Key",
  tasks: "历史任务",
  assistant_sessions: "小助手会话",
};

export function UserDetailDrawer({ user, onClose }: { user: UserDetail; onClose: () => void }) {
  return (
    <Drawer title={user.display_name} description={`账号 ${user.username}`} onClose={onClose} width="max-w-xl">
      <div className="space-y-6 p-6 text-xs">
        <section className="grid grid-cols-2 gap-4 rounded-lg border border-slate-100 bg-slate-50 p-4">
          <div><span className="block text-slate-400">角色</span><strong className="mt-1 block text-slate-700">{user.role === "admin" ? "系统管理员" : "普通用户"}</strong></div>
          <div><span className="block text-slate-400">状态</span><div className="mt-1"><StatusBadge status={user.is_active ? "active" : "inactive"} /></div></div>
          <div><span className="block text-slate-400">活动任务</span><strong className="mt-1 block text-slate-700">{user.active_task_count} / 10</strong></div>
          <div><span className="block text-slate-400">强制改密</span><strong className="mt-1 block text-slate-700">{user.must_change_password ? "是" : "否"}</strong></div>
        </section>
        <section>
          <h3 className="mb-3 text-sm font-medium text-slate-700">禁用影响</h3>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-md border border-slate-100 p-3 text-center"><strong className="block text-lg text-slate-700">{user.impact.active_sessions}</strong><span className="text-slate-400">有效会话</span></div>
            <div className="rounded-md border border-slate-100 p-3 text-center"><strong className="block text-lg text-slate-700">{user.impact.enabled_api_keys}</strong><span className="text-slate-400">启用 Key</span></div>
            <div className="rounded-md border border-slate-100 p-3 text-center"><strong className="block text-lg text-slate-700">{user.impact.active_tasks}</strong><span className="text-slate-400">活动任务</span></div>
          </div>
        </section>
        <section>
          <h3 className="mb-3 text-sm font-medium text-slate-700">资源计数</h3>
          <dl className="divide-y divide-slate-100 rounded-md border border-slate-100">
            {Object.entries(user.resource_counts).map(([key, value]) => <div key={key} className="flex justify-between px-4 py-3"><dt className="text-slate-400">{RESOURCE_LABELS[key] ?? key}</dt><dd className="font-medium text-slate-700">{value}</dd></div>)}
          </dl>
        </section>
      </div>
    </Drawer>
  );
}

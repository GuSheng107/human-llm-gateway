import { type FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, getUser, listUsers, resetUserPassword, updateUser } from "../../api/users";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Icon } from "../../icons";
import type { UserDetail, UserSummary } from "../../types/governance";
import { PasswordResetModal } from "./PasswordResetModal";
import { UserCreateModal } from "./UserCreateModal";
import { UserDetailDrawer } from "./UserDetailDrawer";

const PAGE_SIZE = 20;

export function UsersPage() {
  const [items, setItems] = useState<UserSummary[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<UserSummary | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [oneTime, setOneTime] = useState<{ title: string; password: string } | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const result = await listUsers(page, search);
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    }
  }, [page, search]);

  useEffect(() => void load(), [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const create = async (payload: { username: string; display_name: string; password?: string }) => {
    const result = await createUser(payload);
    setCreating(false);
    if (result.temporary_password) setOneTime({ title: `用户 ${result.username} 已创建`, password: result.temporary_password });
    else notify("用户已创建；请安全交付你设置的临时密码");
    await load();
  };

  const reset = async (password?: string) => {
    if (!resetting) return;
    const result = await resetUserPassword(resetting.id, password);
    setResetting(null);
    if (result.temporary_password) setOneTime({ title: `已重置 ${result.user.username} 的密码`, password: result.temporary_password });
    else notify("密码已重置；现有会话已撤销");
    await load();
  };

  const toggle = async (user: UserSummary) => {
    if (user.is_active) {
      const current = await getUser(user.id);
      const message = `确认禁用 ${user.username}？将撤销 ${current.impact.active_sessions} 个会话、停用 ${current.impact.enabled_api_keys} 个 API Key，并终止 ${current.impact.active_tasks} 个活动任务。`;
      if (!window.confirm(message)) return;
      await updateUser(user.id, { is_active: false });
      notify("用户已禁用，相关会话、Key 和任务已处理");
    } else {
      if (!window.confirm(`确认启用用户 ${user.username}？此前停用的 Key 不会自动恢复。`)) return;
      await updateUser(user.id, { is_active: true });
      notify("用户已启用");
    }
    await load();
  };

  const showDetail = async (user: UserSummary) => setDetail(await getUser(user.id));

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center sm:justify-between">
        <div><h1 className="text-lg font-semibold text-slate-800">用户管理</h1><p className="mt-1 text-xs text-slate-400">创建普通用户，并治理账号状态和临时密码。</p></div>
        <button onClick={() => setCreating(true)} className="inline-flex items-center justify-center gap-2 rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white"><Icon name="plus" className="h-4 w-4" />创建普通用户</button>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <form onSubmit={submitSearch} className="flex gap-2 border-b border-slate-100 p-4"><input value={input} onChange={(event) => setInput(event.target.value)} className="field-input min-w-0 flex-1 sm:max-w-sm" placeholder="搜索账号或显示名" /><button className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-slate-200 px-4 text-xs text-slate-600"><Icon name="search" className="h-3.5 w-3.5" />搜索</button></form>
        {error && <div className="m-4 rounded-md bg-red-50 px-4 py-3 text-xs text-red-600">{error}</div>}
        <div className="overflow-x-auto">
          <table className="min-w-[900px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400"><tr><th className="px-4 py-3 font-medium">账号</th><th className="px-4 py-3 font-medium">显示名</th><th className="px-4 py-3 font-medium">角色</th><th className="px-4 py-3 font-medium">状态</th><th className="px-4 py-3 font-medium">活动任务</th><th className="px-4 py-3 font-medium">最近登录</th><th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">操作</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((user) => <tr key={user.id} className="group hover:bg-slate-50/60"><td className="px-4 py-3 font-mono text-slate-700">{user.username}</td><td className="px-4 py-3 text-slate-600">{user.display_name}</td><td className="px-4 py-3 text-slate-500">{user.role === "admin" ? "管理员" : "普通用户"}</td><td className="px-4 py-3"><StatusBadge status={user.is_active ? "active" : "inactive"} /></td><td className="px-4 py-3 text-slate-500">{user.active_task_count} / 10</td><td className="px-4 py-3 text-slate-500">{user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "-"}</td><td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50"><button onClick={() => void showDetail(user)} className="text-[#409eff]">详情</button>{user.role === "user" && <button onClick={() => setResetting(user)} className="text-amber-600">重置密码</button>}<button onClick={() => void toggle(user)} className={user.is_active ? "text-red-500" : "text-emerald-600"}>{user.is_active ? "禁用" : "启用"}</button></td></tr>)}
              {items.length === 0 && <tr><td colSpan={7} className="px-4 py-12 text-center text-slate-400">暂无用户</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3"><Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} /></div>
      </section>

      {creating && <UserCreateModal onClose={() => setCreating(false)} onSubmit={create} />}
      {resetting && <PasswordResetModal user={resetting} onClose={() => setResetting(null)} onSubmit={reset} />}
      {detail && <UserDetailDrawer user={detail} onClose={() => setDetail(null)} />}
      {oneTime && <Modal title={oneTime.title} description="临时密码关闭后无法再次查看，请立即安全交付给用户。" onClose={() => setOneTime(null)}><div className="space-y-4 p-6"><div className="break-all rounded-md border border-amber-100 bg-amber-50 p-4 font-mono text-sm text-amber-800">{oneTime.password}</div><div className="flex justify-end"><button onClick={() => { void navigator.clipboard.writeText(oneTime.password); notify("已复制临时密码"); }} className="inline-flex items-center gap-2 rounded-md bg-[#409eff] px-4 py-2 text-xs text-white"><Icon name="copy" className="h-4 w-4" />复制</button></div></div></Modal>}
    </div>
  );
}

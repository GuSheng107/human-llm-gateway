import { type FormEvent, useCallback, useEffect, useState } from "react";
import { createUser, listUsers } from "../../api/auth";
import { notify } from "../../components/feedback/Toast";
import type { CurrentUser } from "../../types/auth";

export function UsersPage() {
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  const load = useCallback(async () => {
    setUsers(await listUsers());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await createUser({ username: username.trim(), display_name: displayName.trim(), password });
      notify("用户已创建");
      setFormOpen(false);
      setUsername("");
      setDisplayName("");
      setPassword("");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">用户管理</h1>
          <p className="mt-2 text-xs text-slate-400">创建普通用户账号；管理员账号由环境变量种子创建。</p>
        </div>
        <button
          type="button"
          onClick={() => setFormOpen((open) => !open)}
          className="rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#337ecc]"
        >
          创建用户
        </button>
      </section>

      {formOpen && (
        <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">账号</span>
              <input required value={username} onChange={(event) => setUsername(event.target.value)} placeholder="字母、数字、_ . -" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
              <input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">初始密码</span>
              <input required type="password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位" className="field-input" />
            </label>
          </div>
          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
            <button type="button" onClick={() => setFormOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
            <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
              {submitting ? "创建中…" : "创建"}
            </button>
          </div>
        </form>
      )}

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <th className="px-5 py-3">账号</th>
              <th className="px-4 py-3">显示名</th>
              <th className="px-4 py-3">角色</th>
              <th className="px-5 py-3 text-right">ID</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((user) => (
              <tr key={user.id} className="text-xs">
                <td className="px-5 py-3.5 font-mono text-slate-700">{user.username}</td>
                <td className="px-4 py-3.5 text-slate-600">{user.display_name}</td>
                <td className="px-4 py-3.5">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${
                    user.role === "admin" ? "bg-purple-50 text-purple-600" : "bg-slate-100 text-slate-500"
                  }`}>
                    {user.role === "admin" ? "管理员" : "普通用户"}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-right font-mono text-[10px] text-slate-300">{user.id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

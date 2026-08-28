import { type FormEvent, useState } from "react";
import { Modal } from "../../components/feedback/Modal";

export function UserCreateModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: { username: string; display_name: string; password?: string }) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({
        username: username.trim(),
        display_name: displayName.trim(),
        ...(password ? { password } : {}),
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="创建普通用户" description="后台不能创建或提升管理员。新用户首次登录必须修改临时密码。" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4 p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span><input required value={username} onChange={(event) => setUsername(event.target.value)} className="field-input" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span><input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="field-input" /></label>
        </div>
        <label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">临时密码（可选）</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} className="field-input" placeholder="留空则由服务端安全生成" /></label>
        <p className="text-[11px] leading-5 text-slate-400">自填密码不会在响应中回显；留空生成的密码只显示一次。</p>
        {error && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4"><button type="button" onClick={onClose} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500">取消</button><button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs text-white disabled:opacity-60">{submitting ? "正在创建…" : "创建用户"}</button></div>
      </form>
    </Modal>
  );
}

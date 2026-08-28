import { type FormEvent, useState } from "react";
import { Modal } from "../../components/feedback/Modal";
import type { UserSummary } from "../../types/governance";

export function PasswordResetModal({
  user,
  onClose,
  onSubmit,
}: {
  user: UserSummary;
  onClose: () => void;
  onSubmit: (password?: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit(password || undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重置失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title={`重置 ${user.username} 的密码`} description="现有登录会话将立即撤销，用户下次登录必须修改临时密码。" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4 p-6">
        <label className="block"><span className="mb-1.5 block text-xs font-medium text-slate-600">临时密码（可选）</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="field-input" placeholder="留空则由服务端安全生成" /></label>
        {error && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4"><button type="button" onClick={onClose} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500">取消</button><button disabled={submitting} className="rounded-md bg-red-500 px-4 py-2 text-xs text-white disabled:opacity-60">{submitting ? "正在重置…" : "确认重置"}</button></div>
      </form>
    </Modal>
  );
}

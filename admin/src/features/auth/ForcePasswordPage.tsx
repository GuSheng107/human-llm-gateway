import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { changePassword } from "../../api/auth";
import { useAuth } from "./AuthContext";

export function ForcePasswordPage() {
  const { setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const user = await changePassword(currentPassword, newPassword);
      setUser(user);
      navigate("/console", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "修改密码失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="grid min-h-screen place-items-center bg-[#f4f6f9] px-5 py-10">
      <section className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-8 shadow-[0_12px_40px_rgba(15,23,42,.08)]">
        <div className="mb-6">
          <div className="mb-4 grid h-10 w-10 place-items-center rounded-md bg-amber-500 font-mono font-bold text-white">!</div>
          <h1 className="text-xl font-semibold text-slate-800">首次登录需要修改密码</h1>
          <p className="mt-2 text-xs leading-5 text-slate-400">当前会话仅允许查看账号和修改密码。完成后将直接进入控制台。</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">当前临时密码</span>
            <input autoComplete="current-password" required type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} className="field-input" />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">新密码</span>
            <input autoComplete="new-password" required type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className="field-input" />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">确认新密码</span>
            <input autoComplete="new-password" required type="password" value={confirm} onChange={(event) => setConfirm(event.target.value)} className="field-input" />
          </label>
          <p className="text-[11px] leading-5 text-slate-400">至少 15 个字符；系统不会要求大小写、数字或特殊字符组合。</p>
          {error && <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-500">{error}</div>}
          <button disabled={submitting} className="h-10 w-full rounded-md bg-[#409eff] text-sm font-medium text-white disabled:opacity-60">
            {submitting ? "正在保存…" : "修改密码并继续"}
          </button>
        </form>
        <button type="button" onClick={() => void logout()} className="mt-4 w-full py-2 text-xs text-slate-400 hover:text-slate-600">退出登录</button>
      </section>
    </main>
  );
}

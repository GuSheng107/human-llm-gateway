import { type FormEvent, useState } from "react";
import { changePassword, updateProfile } from "../../api/auth";
import { notify } from "../../components/feedback/Toast";
import { useAuth } from "../auth/AuthContext";

export function AccountPage() {
  const { user, setUser } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [error, setError] = useState("");

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setSavingProfile(true);
    setError("");
    try {
      const updated = await updateProfile(displayName.trim());
      setUser(updated);
      notify("显示名已更新");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSavingProfile(false);
    }
  };

  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSavingPassword(true);
    setError("");
    try {
      const updated = await changePassword(currentPassword, newPassword);
      setUser(updated);
      setCurrentPassword("");
      setNewPassword("");
      setConfirm("");
      notify("密码已修改");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "修改密码失败");
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">账号设置</h1>
        <p className="mt-2 text-xs text-slate-400">管理自己的显示名和登录密码。</p>
      </section>
      {error && <div className="rounded-md border border-red-100 bg-red-50 px-4 py-3 text-xs text-red-600">{error}</div>}

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4"><h2 className="text-sm font-medium text-slate-700">个人资料</h2></div>
        <form onSubmit={saveProfile} className="space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span>
              <input disabled value={user?.username ?? ""} className="field-input bg-slate-50 text-slate-400" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">角色</span>
              <input disabled value={user?.role === "admin" ? "系统管理员" : "普通用户"} className="field-input bg-slate-50 text-slate-400" />
            </label>
          </div>
          <label className="block max-w-md">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
            <input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="field-input" />
          </label>
          <button disabled={savingProfile} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white disabled:opacity-60">
            {savingProfile ? "正在保存…" : "保存资料"}
          </button>
        </form>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4"><h2 className="text-sm font-medium text-slate-700">修改密码</h2></div>
        <form onSubmit={savePassword} className="space-y-4 p-5">
          <label className="block max-w-md">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">当前密码</span>
            <input required type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} className="field-input" />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">新密码</span>
              <input required type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">确认新密码</span>
              <input required type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} className="field-input" />
            </label>
          </div>
          <p className="text-[11px] text-slate-400">新密码至少 15 个字符，允许空格和 Unicode 字符。</p>
          <button disabled={savingPassword} className="rounded-md border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:border-[#409eff] hover:text-[#409eff] disabled:opacity-60">
            {savingPassword ? "正在修改…" : "修改密码"}
          </button>
        </form>
      </section>
    </div>
  );
}

import { type FormEvent, useState } from "react";
import { changePassword, updateProfile } from "../../api/auth";
import { Card } from "../../components/data-display/Card";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
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
      <PageHeader title="账号设置" />
      {error && <ErrorBanner message={error} />}

      <Card>
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="text-sm font-medium text-slate-700">个人资料</h2>
        </div>
        <form onSubmit={saveProfile} className="space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span>
              <input disabled value={user?.username ?? ""} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">角色</span>
              <input
                disabled
                value={user?.role === "admin" ? "系统管理员" : "普通用户"}
                className="field-input"
              />
            </label>
          </div>
          <label className="block max-w-md">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
            <input
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              className="field-input"
            />
          </label>
          <Button type="submit" loading={savingProfile}>
            {savingProfile ? "正在保存…" : "保存资料"}
          </Button>
        </form>
      </Card>

      <Card>
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="text-sm font-medium text-slate-700">修改密码</h2>
        </div>
        <form onSubmit={savePassword} className="space-y-4 p-5">
          <label className="block max-w-md">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">当前密码</span>
            <input
              required
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="field-input"
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">新密码</span>
              <input
                required
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="field-input"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">确认新密码</span>
              <input
                required
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="field-input"
              />
            </label>
          </div>
          <p className="text-caption text-slate-400">至少 10 位，须含英文字母、数字和符号。</p>
          <Button type="submit" variant="ghost" loading={savingPassword}>
            {savingPassword ? "正在修改…" : "修改密码"}
          </Button>
        </form>
      </Card>
    </div>
  );
}

import { type FormEvent, useState } from "react";
import { changePassword, updateProfile } from "../../api/auth";
import { Card } from "../../components/data-display/Card";
import { FormField } from "../../components/form/FormField";
import { PasswordStrength, passwordValid } from "../../components/form/PasswordStrength";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { useAuth } from "../auth/AuthContext";

const EMAIL_PATTERN = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;
const MAX_AVATAR_BYTES = 2 * 1024 * 1024;
const AVATAR_TARGET_BYTES = 1024 * 1024;

function drawCoverPng(img: HTMLImageElement, size: number): string {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法处理图片");
  const min = Math.min(img.width, img.height);
  const sx = (img.width - min) / 2;
  const sy = (img.height - min) / 2;
  ctx.drawImage(img, sx, sy, min, min, 0, 0, size, size);
  return canvas.toDataURL("image/png");
}

function resizeToPng(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        try {
          let result = drawCoverPng(img, 320);
          // 输出超过 1MB 时降级到 200x200 再输出。
          if (result.length > AVATAR_TARGET_BYTES) {
            result = drawCoverPng(img, 200);
          }
          if (result.length > AVATAR_TARGET_BYTES * 2) {
            reject(new Error("头像处理失败"));
            return;
          }
          resolve(result);
        } catch (error) {
          reject(error);
        }
      };
      img.onerror = () => reject(new Error("图片加载失败"));
      img.src = reader.result as string;
    };
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

export function AccountPage() {
  const { user, setUser } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [avatarPreview, setAvatarPreview] = useState<string | null>(user?.avatar_base64 ?? null);
  const [avatarChanged, setAvatarChanged] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [error, setError] = useState("");

  const emailInvalid = email.trim() !== "" && !EMAIL_PATTERN.test(email.trim());
  const mismatch = confirm !== "" && confirm !== newPassword;
  const passwordInvalid = newPassword !== "" && !passwordValid(newPassword);

  const onAvatarChange = async (file: File | null) => {
    if (!file) return;
    if (file.size > MAX_AVATAR_BYTES) {
      setError("头像原图需小于 2MB");
      return;
    }
    try {
      const dataUrl = await resizeToPng(file);
      setAvatarPreview(dataUrl);
      setAvatarChanged(true);
      setError("");
    } catch {
      setError("头像处理失败");
    }
  };

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    if (emailInvalid) {
      setError("邮箱格式不正确");
      return;
    }
    setSavingProfile(true);
    setError("");
    try {
      const updated = await updateProfile({
        display_name: displayName.trim(),
        email: email.trim() || null,
        avatar_base64: avatarChanged ? avatarPreview : undefined,
      });
      setUser(updated);
      setAvatarChanged(false);
      notify("资料已更新");
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
    if (passwordInvalid) {
      setError("新密码需 10-128 位，并包含英文字母、数字和符号");
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

  const avatarSrc = avatarPreview
    ? avatarPreview.startsWith("data:")
      ? avatarPreview
      : `data:image/png;base64,${avatarPreview}`
    : null;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader title="账号设置" />
      {error && <ErrorBanner message={error} />}

      <Card>
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="text-sm font-medium text-slate-700">个人资料</h2>
        </div>
        <form onSubmit={saveProfile} className="space-y-4 p-5">
          <div className="flex items-center gap-4">
            <div className="grid h-16 w-16 shrink-0 place-items-center overflow-hidden rounded-full bg-primary-soft text-lg font-semibold text-primary">
              {avatarSrc ? (
                <img src={avatarSrc} alt="头像" className="h-full w-full object-cover" />
              ) : (
                (user?.display_name || user?.username || "?").slice(0, 1).toUpperCase()
              )}
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-600 transition hover:border-primary hover:text-primary">
              <Icon name="upload" className="h-4 w-4" />
              更换头像
              <input
                type="file"
                accept="image/png,image/jpeg"
                className="hidden"
                onChange={(event) => void onAvatarChange(event.target.files?.[0] ?? null)}
              />
            </label>
            {avatarPreview && (
              <button
                type="button"
                onClick={() => {
                  setAvatarPreview(null);
                  setAvatarChanged(true);
                }}
                className="text-xs text-slate-400 hover:text-red-500"
              >
                移除
              </button>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="登录账号">
              <input disabled value={user?.username ?? ""} className="field-input" />
            </FormField>
            <FormField label="角色">
              <input
                disabled
                value={user?.role === "admin" ? "系统管理员" : "普通用户"}
                className="field-input"
              />
            </FormField>
          </div>
          <FormField label="显示名" required>
            <input
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              className="field-input max-w-md"
            />
          </FormField>
          <FormField label="电子邮箱" error={emailInvalid ? "邮箱格式不正确" : undefined}>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="field-input max-w-md"
              placeholder="选填，用于找回与通知"
            />
          </FormField>
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
          <FormField label="当前密码" required>
            <input
              required
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="field-input max-w-md"
            />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="新密码"
              required
              error={passwordInvalid ? "10-128 位，且包含英文字母、数字和符号" : undefined}
            >
              <input
                required
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                className="field-input"
              />
            </FormField>
            <FormField label="确认新密码" required error={mismatch ? "两次输入的新密码不一致" : undefined}>
              <input
                required
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="field-input"
              />
            </FormField>
          </div>
          <PasswordStrength password={newPassword} />
          <Button type="submit" variant="ghost" loading={savingPassword}>
            {savingPassword ? "正在修改…" : "修改密码"}
          </Button>
        </form>
      </Card>
    </div>
  );
}

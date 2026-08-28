import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { changePassword } from "../../api/auth";
import { FormField } from "../../components/form/FormField";
import { PasswordStrength, passwordValid } from "../../components/form/PasswordStrength";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Button } from "../../components/ui/Button";
import { useAuth } from "./AuthContext";

export function ForcePasswordPage() {
  const { setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const mismatch = confirm !== "" && confirm !== newPassword;
  const passwordInvalid = newPassword !== "" && !passwordValid(newPassword);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (passwordInvalid) {
      setError("新密码需至少 10 位，并包含英文字母、数字和符号");
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
    <main className="relative grid min-h-screen place-items-center overflow-hidden bg-gradient-to-br from-primary-faint via-page to-white px-5 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-primary/15 blur-3xl"
      />
      <section className="relative w-full max-w-md animate-slide-up rounded-2xl border border-white/70 bg-white/80 p-8 shadow-modal backdrop-blur-xl">
        <div className="mb-6">
          <div className="mb-4 grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-amber-400 to-amber-500 font-mono font-bold text-white shadow-card">
            !
          </div>
          <h1 className="text-xl font-semibold text-slate-800">首次登录需要修改密码</h1>
          <p className="mt-2 text-xs leading-5 text-slate-400">当前会话仅允许修改密码。</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <FormField label="当前临时密码" required>
            <input
              autoComplete="current-password"
              required
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="field-input"
            />
          </FormField>
          <FormField
            label="新密码"
            required
            error={passwordInvalid ? "至少 10 位，且包含英文字母、数字和符号" : undefined}
          >
            <input
              autoComplete="new-password"
              required
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="field-input"
            />
          </FormField>
          <FormField
            label="确认新密码"
            required
            error={mismatch ? "两次输入的新密码不一致" : undefined}
          >
            <input
              autoComplete="new-password"
              required
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              className="field-input"
            />
          </FormField>
          <PasswordStrength password={newPassword} />
          {error && <ErrorBanner message={error} />}
          <Button type="submit" size="lg" loading={submitting} className="h-11 w-full">
            {submitting ? "正在保存…" : "修改密码并继续"}
          </Button>
        </form>
        <button
          type="button"
          onClick={() => void logout()}
          className="mt-4 w-full py-2 text-xs text-slate-400 transition hover:text-slate-600"
        >
          退出登录
        </button>
      </section>
    </main>
  );
}

import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { registerAccount } from "../../api/auth";
import { FormField } from "../../components/form/FormField";
import { PasswordStrength, passwordValid } from "../../components/form/PasswordStrength";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";

const EMAIL_PATTERN = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

export function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    invitation_code: "",
    username: "",
    display_name: "",
    email: "",
    password: "",
    confirm: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const field = (name: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [name]: value }));

  const mismatch = form.confirm !== "" && form.confirm !== form.password;
  const passwordInvalid = form.password !== "" && !passwordValid(form.password);
  const emailInvalid = form.email.trim() !== "" && !EMAIL_PATTERN.test(form.email.trim());

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (form.password !== form.confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    if (passwordInvalid) {
      setError("密码需至少 10 位，并包含英文字母、数字和符号");
      return;
    }
    if (emailInvalid) {
      setError("邮箱格式不正确");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await registerAccount({
        invitation_code: form.invitation_code.trim(),
        username: form.username.trim(),
        display_name: form.display_name.trim(),
        password: form.password,
        email: form.email.trim() || null,
      });
      notify("注册成功，请登录");
      navigate("/login", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "注册失败");
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
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-16 h-[28rem] w-[28rem] rounded-full bg-sky-200/40 blur-3xl"
      />

      <section className="relative w-full max-w-lg animate-slide-up rounded-2xl border border-white/70 bg-white/80 p-8 shadow-modal backdrop-blur-xl">
        <div className="mb-7 flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary-light font-mono font-bold text-white shadow-card">
            H
          </div>
          <h1 className="text-xl font-semibold text-slate-800">使用邀请码注册</h1>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <FormField label="邀请码" required>
            <input
              required
              value={form.invitation_code}
              onChange={(event) => field("invitation_code", event.target.value)}
              className="field-input font-mono"
            />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="登录账号" required>
              <input
                autoComplete="username"
                required
                value={form.username}
                onChange={(event) => field("username", event.target.value)}
                className="field-input"
                placeholder="仅 ASCII 字母数字及 . _ -"
              />
            </FormField>
            <FormField label="显示名" required>
              <input
                required
                value={form.display_name}
                onChange={(event) => field("display_name", event.target.value)}
                className="field-input"
              />
            </FormField>
          </div>
          <FormField label="电子邮箱" error={emailInvalid ? "邮箱格式不正确" : undefined}>
            <input
              type="email"
              value={form.email}
              onChange={(event) => field("email", event.target.value)}
              className="field-input"
              placeholder="选填，用于找回与通知"
            />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="密码"
              required
              error={passwordInvalid ? "至少 10 位，且包含英文字母、数字和符号" : undefined}
            >
              <input
                autoComplete="new-password"
                required
                type="password"
                value={form.password}
                onChange={(event) => field("password", event.target.value)}
                className="field-input"
              />
            </FormField>
            <FormField
              label="确认密码"
              required
              error={mismatch ? "两次输入的密码不一致" : undefined}
            >
              <input
                autoComplete="new-password"
                required
                type="password"
                value={form.confirm}
                onChange={(event) => field("confirm", event.target.value)}
                className="field-input"
              />
            </FormField>
          </div>
          <PasswordStrength password={form.password} />
          {error && <ErrorBanner message={error} />}
          <Button
            type="submit"
            size="lg"
            loading={submitting}
            className="h-11 w-full bg-gradient-to-r from-primary to-primary-light hover:brightness-105"
          >
            {submitting ? "正在注册…" : "创建账号"}
          </Button>
        </form>
        <p className="mt-6 text-center text-xs text-slate-400">
          已有账号？{" "}
          <Link to="/login" className="font-medium text-primary hover:underline">
            返回登录
          </Link>
        </p>
      </section>
    </main>
  );
}

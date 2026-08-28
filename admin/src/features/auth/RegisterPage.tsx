import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { registerAccount } from "../../api/auth";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";

export function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    invitation_code: "",
    username: "",
    display_name: "",
    password: "",
    confirm: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (form.password !== form.confirm) {
      setError("两次输入的密码不一致");
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
      });
      notify("注册成功，请登录");
      navigate("/login", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "注册失败");
    } finally {
      setSubmitting(false);
    }
  };

  const field = (name: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [name]: value }));

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
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">邀请码</span>
            <input
              required
              value={form.invitation_code}
              onChange={(event) => field("invitation_code", event.target.value)}
              className="field-input font-mono"
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span>
              <input
                autoComplete="username"
                required
                value={form.username}
                onChange={(event) => field("username", event.target.value)}
                className="field-input"
                placeholder="仅 ASCII 字母数字及 . _ -"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
              <input
                required
                value={form.display_name}
                onChange={(event) => field("display_name", event.target.value)}
                className="field-input"
              />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">密码</span>
              <input
                autoComplete="new-password"
                required
                type="password"
                value={form.password}
                onChange={(event) => field("password", event.target.value)}
                className="field-input"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">确认密码</span>
              <input
                autoComplete="new-password"
                required
                type="password"
                value={form.confirm}
                onChange={(event) => field("confirm", event.target.value)}
                className="field-input"
              />
            </label>
          </div>
          <p className="text-caption leading-5 text-slate-400">
            至少 10 位，须含英文字母、数字和符号。
          </p>
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

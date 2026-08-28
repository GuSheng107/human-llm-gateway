import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { login } from "../../api/client";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Button } from "../../components/ui/Button";
import { useAuth } from "./AuthContext";

export function LoginPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await login(username.trim(), password);
      setUser(result);
      navigate(result.must_change_password ? "/change-password" : "/console", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="relative grid min-h-screen overflow-hidden bg-gradient-to-br from-primary-faint via-page to-white lg:grid-cols-[minmax(440px,44%)_1fr]">
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-primary/15 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-16 h-[28rem] w-[28rem] rounded-full bg-sky-200/40 blur-3xl"
      />

      <section className="relative hidden p-10 lg:flex">
        <div className="relative flex w-full flex-col justify-between overflow-hidden rounded-2xl border border-white/70 bg-gradient-to-br from-white/80 to-primary-soft/50 p-12 shadow-modal backdrop-blur-xl">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 opacity-50"
            style={{
              backgroundImage:
                "radial-gradient(circle, rgba(64,158,255,0.14) 1px, transparent 1px)",
              backgroundSize: "22px 22px",
            }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-primary/20 blur-3xl"
          />

          <div className="relative">
            <div className="flex items-center gap-3">
              <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary-light font-mono text-lg font-bold text-white shadow-card">
                H
              </div>
              <div>
                <div className="text-base font-semibold text-slate-800">Human Gateway</div>
                <div className="mt-0.5 text-caption uppercase tracking-widest text-slate-400">
                  operator console
                </div>
              </div>
            </div>
            <h1 className="mt-12 text-3xl font-semibold leading-tight text-slate-900">
              真人驱动的模型兼容网关
            </h1>
          </div>

          <div className="relative text-caption tracking-wide text-slate-400">
            Human Gateway Administration
          </div>
        </div>
      </section>

      <section className="relative flex items-center justify-center px-5 py-12">
        <div className="w-full max-w-[400px] animate-slide-up">
          <div className="mb-8 lg:hidden">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary-light font-mono font-bold text-white shadow-card">
              H
            </div>
          </div>

          <div className="rounded-2xl border border-white/70 bg-white/80 p-8 shadow-modal backdrop-blur-xl">
            <h2 className="text-xl font-semibold text-slate-800">登录管理台</h2>
            <form onSubmit={submit} className="mt-7 space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">账号</span>
                <input
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  className="field-input"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">密码</span>
                <input
                  autoComplete="current-password"
                  required
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="field-input"
                />
              </label>
              {error && <ErrorBanner message={error} />}
              <Button
                type="submit"
                size="lg"
                loading={submitting}
                className="h-11 w-full bg-gradient-to-r from-primary to-primary-light hover:brightness-105"
              >
                {submitting ? "正在登录…" : "登录"}
              </Button>
            </form>
            <p className="mt-6 text-center text-xs text-slate-400">
              持有邀请码？{" "}
              <Link to="/register" className="font-medium text-primary hover:underline">
                注册账号
              </Link>
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

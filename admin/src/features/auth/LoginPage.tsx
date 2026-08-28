import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { login } from "../../api/client";
import { CaptchaInput } from "../../components/form/CaptchaInput";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Button } from "../../components/ui/Button";
import { useAuth } from "./AuthContext";

const PARTICLES = [
  { left: "6%", top: "18%", size: 6, delay: "0s", duration: "7s" },
  { left: "15%", top: "64%", size: 8, delay: "1.4s", duration: "9s" },
  { left: "28%", top: "30%", size: 5, delay: "2.2s", duration: "6s" },
  { left: "38%", top: "78%", size: 7, delay: "0.8s", duration: "8s" },
  { left: "52%", top: "20%", size: 6, delay: "3s", duration: "7.5s" },
  { left: "64%", top: "58%", size: 9, delay: "1.8s", duration: "10s" },
  { left: "74%", top: "34%", size: 5, delay: "0.4s", duration: "6.5s" },
  { left: "84%", top: "70%", size: 7, delay: "2.6s", duration: "8.5s" },
  { left: "92%", top: "24%", size: 6, delay: "1.1s", duration: "7s" },
];

export function LoginPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [captchaKey, setCaptchaKey] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await login(username.trim(), password, captchaToken, captchaCode);
      setUser(result);
      navigate(result.must_change_password ? "/change-password" : "/console", { replace: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
      setCaptchaCode("");
      setCaptchaKey((key) => key + 1);
    } finally {
      setSubmitting(false);
    }
  };

  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width - 0.5;
    const py = (event.clientY - rect.top) / rect.height - 0.5;
    setTilt({ x: py * -8, y: px * 10 });
  };

  return (
    <main className="relative grid min-h-screen overflow-hidden bg-gradient-to-br from-primary-faint via-page to-white lg:grid-cols-[minmax(440px,44%)_1fr]">
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-primary/15 blur-3xl animate-drift"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-16 h-[28rem] w-[28rem] rounded-full bg-sky-200/50 blur-3xl animate-drift"
        style={{ animationDelay: "-7s" }}
      />
      {PARTICLES.map((particle, index) => (
        <span
          key={index}
          aria-hidden
          className="pointer-events-none absolute rounded-full bg-primary/40 animate-float"
          style={{
            left: particle.left,
            top: particle.top,
            width: particle.size,
            height: particle.size,
            animationDelay: particle.delay,
            animationDuration: particle.duration,
          }}
        />
      ))}

      <section className="relative hidden p-10 lg:flex">
        <div
          className="relative flex w-full flex-col justify-between overflow-hidden rounded-2xl border border-white/70 bg-gradient-to-br from-white/80 to-primary-soft/50 p-12 shadow-modal backdrop-blur-xl transition-transform duration-300"
          style={{ transform: `perspective(1000px) rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)` }}
          onMouseMove={onMove}
          onMouseLeave={() => setTilt({ x: 0, y: 0 })}
        >
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 opacity-50"
            style={{
              backgroundImage:
                "radial-gradient(circle, rgba(64,158,255,0.16) 1px, transparent 1px)",
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
              <div className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">验证码</span>
                <CaptchaInput
                  key={captchaKey}
                  value={captchaCode}
                  onChange={setCaptchaCode}
                  onTokenChange={setCaptchaToken}
                />
              </div>
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
            <div className="mt-6 flex items-center justify-center gap-2 border-t border-slate-100 pt-5 text-xs text-slate-400">
              还没有账号？
              <Link to="/register" className="font-medium text-primary hover:underline">
                使用邀请码注册
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

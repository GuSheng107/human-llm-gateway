import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { login } from "../../api/client";
import { CaptchaInput } from "../../components/form/CaptchaInput";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { PasswordInput } from "../../components/form/PasswordInput";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
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

const REMEMBERED_USERNAME_KEY = "hlg_remembered_username";

type PasswordCredentialWindow = Window & {
  PasswordCredential?: new (data: { id: string; password: string }) => Credential;
};

async function storeBrowserCredential(username: string, password: string): Promise<void> {
  const PasswordCredential = (window as PasswordCredentialWindow).PasswordCredential;
  if (!PasswordCredential || !navigator.credentials?.store) return;
  try {
    await navigator.credentials.store(new PasswordCredential({ id: username, password }));
  } catch {
    // 浏览器未启用密码管理器时静默跳过，不影响登录流程。
  }
}

export function LoginPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState(() => localStorage.getItem(REMEMBERED_USERNAME_KEY) ?? "");
  const [password, setPassword] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [captchaKey, setCaptchaKey] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [rememberPassword, setRememberPassword] = useState(() =>
    Boolean(localStorage.getItem(REMEMBERED_USERNAME_KEY)),
  );
  const [showForgot, setShowForgot] = useState(false);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await login(username.trim(), password, captchaToken, captchaCode);
      if (rememberPassword) {
        localStorage.setItem(REMEMBERED_USERNAME_KEY, username.trim());
        void storeBrowserCredential(username.trim(), password);
      } else {
        localStorage.removeItem(REMEMBERED_USERNAME_KEY);
      }
      // 登录响应中的 access_token 只进 localStorage，不留存在前端状态中。
      const { access_token: _token, token_type: _type, ...user } = result;
      setUser(user);
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
              <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary-light text-white shadow-card">
                <Icon name="gateway" className="h-6 w-6" strokeWidth={1.7} />
              </div>
              <div>
                <div className="text-base font-semibold text-slate-800">能工智人</div>
                <div className="mt-0.5 text-xs uppercase tracking-widest text-slate-400">
                  operator console
                </div>
              </div>
            </div>
            <h1 className="mt-12 text-3xl font-semibold leading-tight text-slate-900">
              真人驱动的模型兼容网关
            </h1>
            <p className="mt-4 max-w-md text-sm leading-6 text-slate-500">
              把每一次 API 请求交给可信的人类与私有模型处理，同时保持熟悉的协议与调用体验。
            </p>

            <div className="mt-10 space-y-3">
              {[
                { icon: "code", title: "协议兼容", detail: "OpenAI Chat / Responses 与 Anthropic 原生接入" },
                { icon: "reply", title: "一键转发", detail: "按策略转发至用户配置的 LLM，保持原协议返回" },
                { icon: "key", title: "安全可控", detail: "API Key、并发名额与凭据加密隔离" },
              ].map((feature) => (
                <div
                  key={feature.title}
                  className="flex items-center gap-4 rounded-xl border border-white/80 bg-white/65 px-4 py-4 shadow-sm backdrop-blur"
                >
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-primary-faint text-primary">
                    <Icon name={feature.icon} className="h-5 w-5" strokeWidth={1.8} />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-slate-700">{feature.title}</div>
                    <div className="mt-1 text-xs leading-5 text-slate-400">{feature.detail}</div>
                  </div>
                </div>
              ))}
            </div>

            <div className="relative mt-8 overflow-hidden rounded-xl border border-primary/10 bg-white/55 p-4">
              <div className="flex items-center justify-between text-xs font-medium uppercase tracking-widest text-slate-400">
                <span>request route</span>
                <span className="flex items-center gap-1.5 text-emerald-600">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  ready
                </span>
              </div>
              <div className="mt-4 flex items-center gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg bg-slate-800 px-2.5 py-2 text-white">
                  <Icon name="code" className="h-5 w-5 shrink-0 text-sky-200" />
                  <div className="min-w-0">
                    <div className="text-xs font-semibold">API 请求</div>
                    <div className="truncate text-[11px] text-slate-300">兼容协议</div>
                  </div>
                </div>
                <Icon name="chevronRight" className="h-4 w-4 shrink-0 text-primary" />
                <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-primary/20 bg-primary-faint px-2.5 py-2 text-primary">
                  <Icon name="gateway" className="h-5 w-5 shrink-0" />
                  <div className="min-w-0">
                    <div className="text-xs font-semibold">网关路由</div>
                    <div className="truncate text-[11px] text-primary/70">模型与策略</div>
                  </div>
                </div>
                <Icon name="chevronRight" className="h-4 w-4 shrink-0 text-primary" />
                <div className="flex min-w-0 flex-1 flex-col gap-1.5 rounded-lg border border-sky-100 bg-white/80 px-2.5 py-2 text-[11px] text-slate-600">
                  <span className="flex items-center gap-1.5"><Icon name="reply" className="h-3.5 w-3.5 text-primary" />人工回复</span>
                  <span className="flex items-center gap-1.5"><Icon name="cpu" className="h-3.5 w-3.5 text-primary" />一键转发</span>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <span className="h-px flex-1 bg-gradient-to-r from-primary/20 to-primary/60" />
                <span className="rounded-full border border-primary/15 bg-primary-faint px-3 py-1.5 text-[11px] font-medium text-primary">
                  按原协议返回 Fake Model 响应
                </span>
                <span className="h-px flex-1 bg-gradient-to-r from-primary/60 to-primary/20" />
              </div>
              <div className="mt-2 text-center text-xs leading-5 text-slate-500">请求身份、回复策略与模型权限全程可追踪</div>
            </div>
          </div>

          <div className="relative text-xs tracking-wide text-slate-400">
            能工智人管理台
          </div>
        </div>
      </section>

      <section className="relative flex items-center justify-center px-5 py-12">
        <div className="w-full max-w-[400px] animate-slide-up">
          <div className="mb-8 lg:hidden">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-primary to-primary-light text-white shadow-card">
              <Icon name="gateway" className="h-6 w-6" strokeWidth={1.7} />
            </div>
          </div>

          <div className="rounded-2xl border border-white/70 bg-white/80 p-8 shadow-modal backdrop-blur-xl">
            <h2 className="text-xl font-semibold text-slate-800">登录管理台</h2>
            <p className="mt-1.5 text-xs text-slate-400">使用账号与密码访问能工智人网关</p>
            <form onSubmit={submit} className="mt-7 space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">账号</span>
                <input
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  className="field-input"
                  placeholder="登录账号"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 flex items-center justify-between text-xs font-medium text-slate-600">
                  密码
                  <button
                    type="button"
                    onClick={() => setShowForgot(true)}
                    className="font-normal text-slate-400 transition-colors hover:text-primary"
                  >
                    忘记密码？
                  </button>
                </span>
                <PasswordInput
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="账号密码"
                />
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={rememberPassword}
                  onChange={(event) => setRememberPassword(event.target.checked)}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-primary focus:ring-primary/30"
                />
                记住密码
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

      {showForgot && (
        <Modal title="忘记密码" onClose={() => setShowForgot(false)} width="max-w-md">
          <div className="space-y-4 px-6 pb-6 pt-2 text-sm leading-6 text-slate-600">
            <p>
              本系统不提供自助找回密码入口。请联系系统管理员，在后台
              <span className="mx-1 rounded bg-primary-faint px-1.5 py-0.5 text-xs text-primary">
                系统设置 · 用户管理
              </span>
              中为你的账号重置密码。
            </p>
            <p className="text-xs leading-5 text-slate-400">
              重置后你将获得一个临时密码，使用它登录管理台后需立即设置新密码。为保护账号安全，请勿通过本页面之外的渠道透露验证码信息。
            </p>
          </div>
        </Modal>
      )}
    </main>
  );
}

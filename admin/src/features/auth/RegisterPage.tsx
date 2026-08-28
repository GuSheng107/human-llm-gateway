import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { registerAccount } from "../../api/auth";
import { notify } from "../../components/feedback/Toast";

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
    <main className="grid min-h-screen place-items-center bg-[#f4f6f9] px-5 py-10">
      <section className="w-full max-w-lg rounded-lg border border-slate-200 bg-white p-8 shadow-[0_12px_40px_rgba(15,23,42,.08)]">
        <div className="mb-7 flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md bg-[#409eff] font-mono font-bold text-white">H</div>
          <div>
            <h1 className="text-xl font-semibold text-slate-800">使用邀请码注册</h1>
            <p className="mt-1 text-xs text-slate-400">创建你的 Human Gateway 账号</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">邀请码</span>
            <input required value={form.invitation_code} onChange={(event) => field("invitation_code", event.target.value)} className="field-input font-mono" />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span>
              <input autoComplete="username" required value={form.username} onChange={(event) => field("username", event.target.value)} className="field-input" placeholder="仅 ASCII 字母数字及 . _ -" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
              <input required value={form.display_name} onChange={(event) => field("display_name", event.target.value)} className="field-input" />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">密码</span>
              <input autoComplete="new-password" required type="password" value={form.password} onChange={(event) => field("password", event.target.value)} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">确认密码</span>
              <input autoComplete="new-password" required type="password" value={form.confirm} onChange={(event) => field("confirm", event.target.value)} className="field-input" />
            </label>
          </div>
          <p className="text-[11px] leading-5 text-slate-400">密码至少 15 个字符，允许空格和 Unicode 字符。</p>
          {error && <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-500">{error}</div>}
          <button disabled={submitting} className="flex h-10 w-full items-center justify-center rounded-md bg-[#409eff] text-sm font-medium text-white transition hover:bg-[#337ecc] disabled:opacity-60">
            {submitting ? "正在注册…" : "创建账号"}
          </button>
        </form>
        <p className="mt-6 text-center text-xs text-slate-400">
          已有账号？ <Link to="/login" className="text-[#409eff] hover:underline">返回登录</Link>
        </p>
      </section>
    </main>
  );
}

import { useCallback, useEffect, useState } from "react";
import { fetchCaptcha } from "../../api/auth";
import { Icon } from "../../icons";

export function CaptchaInput({
  value,
  onChange,
  onTokenChange,
}: {
  value: string;
  onChange: (value: string) => void;
  onTokenChange: (token: string) => void;
}) {
  const [image, setImage] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchCaptcha();
      setImage(result.captcha_image);
      onTokenChange(result.captcha_token);
      onChange("");
    } catch {
      // 静默失败，用户可点击重试
    } finally {
      setLoading(false);
    }
  }, [onChange, onTokenChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="flex gap-2">
      <button
        type="button"
        onClick={() => void refresh()}
        className="h-10 w-[112px] shrink-0 overflow-hidden rounded-md border border-slate-200 bg-slate-50"
        title="点击刷新验证码"
        aria-label="点击刷新验证码"
      >
        {image ? (
          <img src={image} alt="验证码" className="h-full w-full object-cover" />
        ) : (
          <span className="flex h-full items-center justify-center text-xs text-slate-400">
            加载中…
          </span>
        )}
      </button>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="field-input min-w-0 flex-1"
        placeholder="验证码"
        maxLength={8}
        autoComplete="off"
      />
      <button
        type="button"
        onClick={() => void refresh()}
        className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-slate-200 text-slate-500 transition hover:border-primary hover:text-primary"
        title="刷新"
        aria-label="刷新验证码"
      >
        <Icon name="refresh" className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
      </button>
    </div>
  );
}

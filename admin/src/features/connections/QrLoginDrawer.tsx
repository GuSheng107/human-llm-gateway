import { useCallback, useEffect, useRef, useState } from "react";
import { pollQrLogin, startQrLogin } from "../../api/connections";
import type { ImConnection } from "../../types/gateway";
import { Button } from "../../components/ui/Button";
import { friendlyErrorMessage } from "../../utils/notify";

type QrPhase = "loading" | "wait" | "scanned" | "saving" | "success" | "expired" | "error";

const POLL_INTERVAL_MS = 2000;
const QR_TTL_SECONDS = 300;

const PHASE_TEXT: Record<QrPhase, string> = {
  loading: "正在获取二维码…",
  wait: "请用微信扫一扫二维码",
  scanned: "已扫码，请在手机上点击确认",
  saving: "登录成功，正在保存凭据…",
  success: "扫码绑定已完成",
  expired: "二维码已过期",
  error: "出错了，请重试",
};

interface QrLoginSectionProps {
  connection: ImConnection;
  onBound: () => void;
  disabled?: boolean;
}

export function QrLoginSection({ connection, onBound, disabled = false }: QrLoginSectionProps) {
  const [phase, setPhase] = useState<QrPhase>(connection.bound ? "success" : "loading");
  const [qrImage, setQrImage] = useState("");
  const [errorText, setErrorText] = useState("");
  const [remaining, setRemaining] = useState(QR_TTL_SECONDS);
  const abortRef = useRef<AbortController | null>(null);

  const stopPolling = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const beginLogin = useCallback(
    async (id: string) => {
      if (disabled) return;
      stopPolling();
      setPhase("loading");
      setErrorText("");
      setRemaining(QR_TTL_SECONDS);
      try {
        const started = await startQrLogin(id);
        setQrImage(`data:image/png;base64,${started.qrcode_img_content}`);
        setPhase("wait");
      } catch (caught) {
        setPhase("error");
        setErrorText(friendlyErrorMessage(caught, "获取二维码失败"));
        return;
      }
      const controller = new AbortController();
      abortRef.current = controller;
      const poll = async () => {
        if (controller.signal.aborted) return;
        try {
          const result = await pollQrLogin(id);
          if (controller.signal.aborted) return;
          if (result.status === "confirmed") {
            stopPolling();
            setPhase("saving");
            setPhase("success");
            onBound();
            return;
          }
          if (result.status === "expired") {
            stopPolling();
            setPhase("expired");
            return;
          }
          setPhase(result.status === "scaned" ? "scanned" : "wait");
        } catch (caught) {
          if (!controller.signal.aborted) {
            stopPolling();
            setPhase("error");
            setErrorText(friendlyErrorMessage(caught, "轮询登录状态失败"));
          }
          return;
        }
        if (!controller.signal.aborted) {
          window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
        }
      };
      void poll();
    },
    [onBound, stopPolling, disabled]
  );

  useEffect(() => {
    setQrImage("");
    if (connection.bound) {
      setPhase("success");
    } else if (disabled) {
      setPhase("wait");
    } else {
      const timer = window.setTimeout(() => void beginLogin(connection.id), 0);
      return () => {
        window.clearTimeout(timer);
        stopPolling();
      };
    }
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection.id]);

  useEffect(() => {
    if (phase !== "wait" && phase !== "scanned") return;
    const timer = window.setInterval(() => {
      setRemaining((value) => {
        if (value <= 1) {
          stopPolling();
          setPhase("expired");
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [phase, stopPolling]);

  const minutes = Math.floor(remaining / 60);
  const seconds = String(remaining % 60).padStart(2, "0");

  return (
    <section className="rounded-xl border border-emerald-100 bg-emerald-50/40 p-4">
      <div className="flex flex-col items-center gap-4 py-2">
        {phase === "loading" && (
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
        )}
        {phase !== "loading" && phase !== "success" && (
          <div className="relative grid h-[240px] w-[240px] place-items-center rounded-lg border-2 border-slate-200 bg-white">
            {qrImage ? (
              <img src={qrImage} alt="登录二维码" className="h-full w-full rounded-md object-contain" />
            ) : (
              <span className="text-xs text-slate-300">二维码获取失败</span>
            )}
            {phase === "expired" && !disabled && (
              <div className="absolute inset-0 grid place-items-center rounded-lg bg-white/95">
                <Button onClick={() => void beginLogin(connection.id)}>
                  刷新二维码
                </Button>
              </div>
            )}
          </div>
        )}
        <p
          className={`text-sm ${
            phase === "scanned" || phase === "saving" || phase === "success"
              ? "text-emerald-600"
              : phase === "expired" || phase === "error"
                ? "text-red-500"
                : "text-slate-600"
          }`}
        >
          {phase === "error" && errorText ? `出错：${errorText}` : PHASE_TEXT[phase]}
        </p>
        {(phase === "wait" || phase === "scanned") && (
          <p className="font-mono text-xs text-slate-400">
            {minutes}:{seconds} 后过期
          </p>
        )}
        {phase === "error" && !disabled && (
          <Button variant="ghost" onClick={() => void beginLogin(connection.id)}>
            重试
          </Button>
        )}
        {phase === "success" && !disabled && (
          <Button variant="ghost" onClick={() => void beginLogin(connection.id)}>
            重新扫码绑定
          </Button>
        )}
        {disabled && (
          <p className="text-xs text-amber-600 mt-2">管理员只读视图，不可执行扫码绑定操作</p>
        )}
      </div>
    </section>
  );
}

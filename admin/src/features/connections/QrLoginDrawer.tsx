import { useCallback, useEffect, useRef, useState } from "react";
import { pollQrLogin, startQrLogin, updateConnection } from "../../api/connections";
import type { ImConnection } from "../../types/gateway";
import { Drawer } from "../../components/feedback/Drawer";
import { Button } from "../../components/ui/Button";

type QrPhase = "loading" | "wait" | "scanned" | "saving" | "expired" | "error";

const POLL_INTERVAL_MS = 2000;
const QR_TTL_SECONDS = 300;

const PHASE_TEXT: Record<QrPhase, string> = {
  loading: "正在获取二维码…",
  wait: "请用微信扫一扫二维码",
  scanned: "已扫码，请在手机上点击确认",
  saving: "登录成功，正在保存凭据…",
  expired: "二维码已过期",
  error: "出错了，请重试",
};

interface QrLoginDrawerProps {
  connection: ImConnection | null;
  onClose: () => void;
  onSaved: () => void;
}

export function QrLoginDrawer({ connection, onClose, onSaved }: QrLoginDrawerProps) {
  const [phase, setPhase] = useState<QrPhase>("loading");
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
        setErrorText(caught instanceof Error ? caught.message : "获取二维码失败");
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
            try {
              // 扫码确认后把 bot_token 写回连接配置（secret 合并写）。
              await updateConnection(id, { config: { token: result.bot_token ?? "" } });
              onSaved();
              onClose();
              return;
            } catch (caught) {
              setPhase("error");
              setErrorText(caught instanceof Error ? caught.message : "保存凭据失败");
              return;
            }
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
            setErrorText(caught instanceof Error ? caught.message : "轮询登录状态失败");
          }
          return;
        }
        if (!controller.signal.aborted) {
          window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
        }
      };
      void poll();
    },
    [onClose, onSaved, stopPolling]
  );

  useEffect(() => {
    if (connection) {
      setQrImage("");
      void beginLogin(connection.id);
    }
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection?.id]);

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

  if (!connection) return null;

  return (
    <Drawer title={`扫码登录 · ${connection.name}`} onClose={onClose} width="max-w-[480px]">
      <div className="flex flex-col items-center gap-4 py-4">
        {phase === "loading" && (
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
        )}
        {phase !== "loading" && (
          <div className="relative grid h-[240px] w-[240px] place-items-center rounded-lg border-2 border-slate-200 bg-white">
            {qrImage ? (
              <img src={qrImage} alt="登录二维码" className="h-full w-full rounded-md object-contain" />
            ) : (
              <span className="text-xs text-slate-300">二维码获取失败</span>
            )}
            {phase === "expired" && (
              <div className="absolute inset-0 grid place-items-center rounded-lg bg-white/95">
                <Button onClick={() => connection && void beginLogin(connection.id)}>
                  刷新二维码
                </Button>
              </div>
            )}
          </div>
        )}
        <p
          className={`text-sm ${
            phase === "scanned" || phase === "saving"
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
        {phase === "error" && (
          <Button variant="ghost" onClick={() => connection && void beginLogin(connection.id)}>
            重试
          </Button>
        )}
        <button
          type="button"
          className="mt-2 text-xs text-slate-400 underline-offset-2 transition hover:text-slate-600 hover:underline"
          onClick={() => {
            stopPolling();
            onClose();
          }}
        >
          使用其他方式登录（绑定码）
        </button>
      </div>
    </Drawer>
  );
}

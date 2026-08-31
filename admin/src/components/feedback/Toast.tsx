import { useCallback, useEffect, useState } from "react";
import { Icon } from "../../icons";

export type ToastVariant = "info" | "success" | "error";
type Notice = { id: number; message: string; variant: ToastVariant };

let globalNotify: ((message: string, variant: ToastVariant) => void) | null = null;
let nextNoticeId = 1;

export function notify(message: string, variant: ToastVariant = "info") {
  globalNotify?.(message, variant);
}

export function ToastHost() {
  const [notice, setNotice] = useState<Notice | null>(null);

  const push = useCallback((message: string, variant: ToastVariant) => {
    const next = { id: nextNoticeId++, message, variant };
    setNotice(next);
    window.setTimeout(
      () => setNotice((current) => (current?.id === next.id ? null : current)),
      3200,
    );
  }, []);

  useEffect(() => {
    globalNotify = push;
    return () => {
      globalNotify = null;
    };
  }, [push]);

  if (!notice) return null;
  // Element Plus Message 风格：顶部居中、浅色底、左侧彩色图标、圆角与轻阴影。
  const tone =
    notice.variant === "error"
      ? { icon: "close-circle", color: "text-red-500", border: "border-red-200" }
      : notice.variant === "success"
        ? { icon: "check-circle", color: "text-emerald-500", border: "border-emerald-200" }
        : { icon: "info-circle", color: "text-sky-500", border: "border-sky-200" };
  return (
    <div
      role={notice.variant === "error" ? "alert" : "status"}
      className={`fixed top-5 left-1/2 z-60 flex w-fit max-w-sm -translate-x-1/2 items-center gap-2 rounded-md border bg-white px-4 py-2.5 text-xs text-slate-600 shadow-md animate-toast-in ${tone.border}`}
    >
      <Icon name={tone.icon} className={`h-4 w-4 shrink-0 ${tone.color}`} />
      <span>{notice.message}</span>
    </div>
  );
}

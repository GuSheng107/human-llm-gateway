import { useCallback, useEffect, useState } from "react";

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
  const tone =
    notice.variant === "error"
      ? "bg-red-600"
      : notice.variant === "success"
        ? "bg-emerald-600"
        : "bg-slate-800";
  return (
    <div
      role={notice.variant === "error" ? "alert" : "status"}
      className={`fixed bottom-5 right-5 z-60 max-w-sm rounded-md px-4 py-3 text-xs text-white shadow-modal animate-slide-up ${tone}`}
    >
      {notice.message}
    </div>
  );
}

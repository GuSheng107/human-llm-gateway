import { useCallback, useEffect, useState, type ReactNode } from "react";

let globalNotify: ((message: string) => void) | null = null;

export function notify(message: string) {
  globalNotify?.(message);
}

export function ToastHost() {
  const [notice, setNotice] = useState("");

  const push = useCallback((message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice((current) => (current === message ? "" : current)), 3200);
  }, []);

  useEffect(() => {
    globalNotify = push;
    return () => {
      globalNotify = null;
    };
  }, [push]);

  if (!notice) return null;
  return (
    <div
      role="status"
      className="fixed bottom-5 right-5 z-[70] max-w-sm rounded-md bg-slate-800 px-4 py-3 text-xs text-white shadow-xl"
    >
      {notice}
    </div>
  );
}

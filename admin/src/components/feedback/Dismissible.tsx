import { useState, type ReactNode } from "react";

/** 可关闭的说明区块：关闭后写入 localStorage，之后不再显示。 */
export function Dismissible({ id, children }: { id: string; children: ReactNode }) {
  const key = `hlg_dismissed_${id}`;
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(key) === "1");
  if (dismissed) return null;
  return (
    <div className="mt-2 flex items-start gap-2">
      <div className="flex-1">{children}</div>
      <button
        type="button"
        aria-label="关闭说明"
        onClick={() => {
          localStorage.setItem(key, "1");
          setDismissed(true);
        }}
        className="rounded p-0.5 text-slate-300 transition hover:text-slate-500"
      >
        ×
      </button>
    </div>
  );
}

import { useEffect } from "react";
import { Icon } from "../../icons";

export function useEscapeKey(onClose: () => void) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
}

export function OverlayHeader({
  title,
  description,
  onClose,
}: {
  title: string;
  description?: string;
  onClose: () => void;
}) {
  return (
    <header className="flex items-start justify-between border-b border-slate-100 px-6 py-4">
      <div>
        <h2 className="text-base font-semibold text-slate-800">{title}</h2>
        {description && <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>}
      </div>
      <button
        type="button"
        onClick={onClose}
        className="rounded p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
        aria-label="关闭"
      >
        <Icon name="close" className="h-4 w-4" />
      </button>
    </header>
  );
}

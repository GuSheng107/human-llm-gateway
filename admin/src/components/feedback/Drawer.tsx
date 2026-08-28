import type { ReactNode } from "react";
import { OverlayHeader, useEscapeKey } from "./Overlay";

interface DrawerProps {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}

export function Drawer({ title, description, onClose, children, width = "max-w-2xl" }: DrawerProps) {
  useEscapeKey(onClose);

  return (
    <div
      className="fixed inset-0 z-40 bg-slate-900/30 animate-fade-in"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        className={`absolute inset-y-0 right-0 flex w-full ${width} flex-col border-l border-slate-200 bg-white shadow-drawer animate-slide-in-right`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <OverlayHeader title={title} description={description} onClose={onClose} />
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}

import type { ReactNode } from "react";
import { OverlayHeader, useEscapeKey } from "./Overlay";

interface DrawerProps {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
  side?: "left" | "right";
}

export function Drawer({
  title,
  description,
  onClose,
  children,
  width = "max-w-2xl",
  side = "right",
}: DrawerProps) {
  useEscapeKey(onClose);

  return (
    <div
      className="fixed inset-0 z-40 bg-slate-900/30 animate-fade-in"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        className={`absolute inset-y-0 flex w-full ${width} flex-col bg-white shadow-drawer ${
          side === "left"
            ? "left-0 border-r border-slate-200 animate-slide-in-left"
            : "right-0 border-l border-slate-200 animate-slide-in-right"
        }`}
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

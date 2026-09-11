import type { ReactNode } from "react";
import { OverlayHeader, useDialogFocus } from "./Overlay";

interface ModalProps {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}

export function Modal({ title, description, onClose, children, width = "max-w-xl" }: ModalProps) {
  const dialogRef = useDialogFocus(onClose);

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/35 p-4 backdrop-blur-[1px] animate-fade-in"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        ref={dialogRef}
        tabIndex={-1}
        className={`w-full ${width} max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-modal animate-scale-in`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <OverlayHeader title={title} description={description} onClose={onClose} />
        {children}
      </section>
    </div>
  );
}

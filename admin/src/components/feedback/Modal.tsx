import { useEffect, type ReactNode } from "react";
import { Icon } from "../../icons";

interface ModalProps {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}

export function Modal({ title, description, onClose, children, width = "max-w-xl" }: ModalProps) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/35 p-4 backdrop-blur-[1px]"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        className={`w-full ${width} overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="flex items-start justify-between border-b border-slate-100 px-6 py-5">
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
        {children}
      </section>
    </div>
  );
}

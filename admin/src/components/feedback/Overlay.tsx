import { useEffect, useRef } from "react";
import { Icon } from "../../icons";

const dialogStack: HTMLElement[] = [];
const focusSelector = 'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function useDialogFocus(onClose: () => void) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogStack.push(dialog);
    const candidates = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusSelector))
      .filter(element => element.tabIndex >= 0 && !element.closest('[hidden], [aria-hidden="true"]'));
    const input = dialog.querySelector<HTMLElement>('input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled)');
    (input ?? candidates()[0] ?? dialog).focus();
    const handleKey = (event: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== dialog) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      }
      if (event.key === "Tab") {
        const focusable = candidates();
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!first || !dialog.contains(document.activeElement)) {
          event.preventDefault();
          (first ?? dialog).focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKey, true);
    return () => {
      document.removeEventListener("keydown", handleKey, true);
      const index = dialogStack.indexOf(dialog);
      if (index >= 0) dialogStack.splice(index, 1);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return dialogRef;
}

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

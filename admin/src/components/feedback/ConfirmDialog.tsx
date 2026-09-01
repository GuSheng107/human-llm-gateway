import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../../icons";
import { Button } from "../ui/Button";

type ConfirmVariant = "primary" | "danger";

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
}

interface PendingConfirm extends Required<ConfirmOptions> {
  resolve: (confirmed: boolean) => void;
}

let requestConfirm: ((options: ConfirmOptions) => Promise<boolean>) | null = null;

export function confirmAction(options: ConfirmOptions): Promise<boolean> {
  if (!requestConfirm) return Promise.resolve(false);
  return requestConfirm(options);
}

export function ConfirmDialogHost() {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const close = useCallback((confirmed: boolean) => {
    setPending((current) => {
      current?.resolve(confirmed);
      return null;
    });
  }, []);

  useEffect(() => {
    requestConfirm = (options) =>
      new Promise<boolean>((resolve) => {
        setPending((current) => {
          current?.resolve(false);
          return {
            title: options.title ?? "请确认操作",
            message: options.message,
            confirmLabel: options.confirmLabel ?? "确定",
            cancelLabel: options.cancelLabel ?? "取消",
            variant: options.variant ?? "danger",
            resolve,
          };
        });
      });
    return () => {
      requestConfirm = null;
      setPending((current) => {
        current?.resolve(false);
        return null;
      });
    };
  }, []);

  useEffect(() => {
    if (!pending) return;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, pending]);

  if (!pending) return null;

  return (
    <div
      className="fixed inset-0 z-70 grid place-items-center bg-slate-900/35 p-4 backdrop-blur-[1px] animate-fade-in"
      onMouseDown={(event) => event.target === event.currentTarget && close(false)}
    >
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="global-confirm-title"
        aria-describedby="global-confirm-message"
        className="w-full max-w-md overflow-hidden rounded-lg border border-slate-200 bg-white shadow-modal animate-scale-in"
      >
        <div className="flex gap-3 px-5 pt-5">
          <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${pending.variant === "danger" ? "bg-red-50 text-red-500" : "bg-blue-50 text-primary"}`}>
            <Icon name={pending.variant === "danger" ? "warning" : "info-circle"} className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h2 id="global-confirm-title" className="text-sm font-semibold text-slate-800">
              {pending.title}
            </h2>
            <p id="global-confirm-message" className="mt-2 text-xs leading-5 text-slate-500">
              {pending.message}
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <Button ref={cancelRef} variant="ghost" onClick={() => close(false)}>
            {pending.cancelLabel}
          </Button>
          <Button variant={pending.variant === "danger" ? "danger" : "primary"} onClick={() => close(true)}>
            {pending.confirmLabel}
          </Button>
        </div>
      </section>
    </div>
  );
}

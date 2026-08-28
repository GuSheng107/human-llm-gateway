import type { ReactNode } from "react";

export function FormField({
  label,
  required,
  error,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-0.5 text-xs font-medium text-slate-600">
        {required && <span className="text-danger">*</span>}
        {label}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-xs leading-5 text-red-500">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-caption leading-5 text-slate-400">{hint}</span>
      ) : null}
    </label>
  );
}

import type { ReactNode } from "react";

export function Card({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-card ${className}`}>
      {children}
    </section>
  );
}

import type { ReactNode } from "react";
import { Dismissible } from "../feedback/Dismissible";

export function PageHeader({
  title,
  description,
  actions,
  dismissId,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  /** 提供后 description 首次显示且可关闭，关闭后写入 localStorage。 */
  dismissId?: string;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-card sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-lg font-semibold text-slate-800">{title}</h1>
        {description &&
          (dismissId ? (
            <Dismissible id={dismissId}>
              <p className="text-xs text-slate-400">{description}</p>
            </Dismissible>
          ) : (
            <p className="mt-1 text-xs text-slate-400">{description}</p>
          ))}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </section>
  );
}

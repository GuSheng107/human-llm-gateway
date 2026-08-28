import { Icon } from "../../icons";

export function ErrorBanner({
  message,
  className = "",
}: {
  message: string;
  className?: string;
}) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className={`flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-4 py-3 text-xs text-red-600 ${className}`}
    >
      <Icon name="warning" className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="leading-5">{message}</span>
    </div>
  );
}

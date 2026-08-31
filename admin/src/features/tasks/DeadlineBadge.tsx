import { useEffect, useState } from "react";

/** 实时倒计时：距 human_deadline_at 的剩余秒数，每秒刷新。 */
function useCountdown(deadline: string | null): number | null {
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(() =>
    deadline ? Math.max(0, Math.floor((new Date(deadline).getTime() - Date.now()) / 1000)) : null,
  );
  useEffect(() => {
    if (!deadline) {
      setRemainingSeconds(null);
      return;
    }
    const tick = () =>
      setRemainingSeconds(
        Math.max(0, Math.floor((new Date(deadline).getTime() - Date.now()) / 1000)),
      );
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);
  return remainingSeconds;
}

export function formatRemaining(seconds: number): string {
  if (seconds <= 0) return "已超时";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** 人工截止倒计时徽标：<5min 红 / 5-30min 黄 / >30min 灰。 */
export function DeadlineBadge({ deadline }: { deadline: string | null }) {
  const remaining = useCountdown(deadline);
  if (remaining === null) return <span className="text-slate-300">-</span>;
  const tone =
    remaining <= 0 || remaining < 300
      ? "bg-red-50 text-red-600"
      : remaining < 1800
        ? "bg-amber-50 text-amber-600"
        : "bg-slate-100 text-slate-500";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 font-mono text-xs ${tone}`}
    >
      {formatRemaining(remaining)}
    </span>
  );
}

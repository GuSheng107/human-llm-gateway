const STATUS_META: Record<string, { label: string; className: string; dot: string }> = {
  online: { label: "在线", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  starting: { label: "启动中", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500 animate-pulse" },
  auth_required: { label: "需要认证", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  connecting: { label: "连接中", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500 animate-pulse" },
  offline: { label: "离线", className: "border-slate-200 bg-slate-50 text-slate-500", dot: "bg-slate-400" },
  stopped: { label: "已停止", className: "border-slate-200 bg-slate-50 text-slate-400", dot: "bg-slate-300" },
  pending_restart: { label: "待重启", className: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  waiting: { label: "等待绑定", className: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  bound: { label: "已绑定", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  unbound: { label: "未绑定", className: "border-slate-200 bg-slate-50 text-slate-500", dot: "bg-slate-400" },
  expired: { label: "已过期", className: "border-slate-200 bg-slate-50 text-slate-400", dot: "bg-slate-300" },
  locked: { label: "已锁定", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  completed: { label: "已完成", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  human_waiting: { label: "等待人工", className: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  waiting_human: { label: "等待人工", className: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  received: { label: "已接收", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  response_ready: { label: "待输出", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500 animate-pulse" },
  forwarding_llm: { label: "LLM 处理", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  llm_streaming: { label: "LLM 处理", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  responding: { label: "输出中", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500 animate-pulse" },
  pseudo_streaming: { label: "输出中", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  timeout: { label: "超时", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  timed_out: { label: "超时", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  failed: { label: "失败", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  cancelled: { label: "已取消", className: "border-slate-200 bg-slate-50 text-slate-400", dot: "bg-slate-300" },
  active: { label: "启用", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  inactive: { label: "停用", className: "border-slate-200 bg-slate-50 text-slate-400", dot: "bg-slate-300" },
  revoked: { label: "已撤销", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  exhausted: { label: "已用尽", className: "border-slate-200 bg-slate-50 text-slate-500", dot: "bg-slate-400" },
  // 通用结果/级别语义（审计结果、应用日志级别等）
  success: { label: "成功", className: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  denied: { label: "已拒绝", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  warning: { label: "警告", className: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  info: { label: "信息", className: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500" },
  debug: { label: "调试", className: "border-slate-200 bg-slate-50 text-slate-500", dot: "bg-slate-400" },
  error: { label: "错误", className: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
};

export function StatusBadge({ status, fallback }: { status: string; fallback?: string }) {
  const meta = STATUS_META[status] ?? {
    label: fallback ?? status,
    className: "border-slate-200 bg-slate-50 text-slate-500",
    dot: "bg-slate-400",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-caption font-medium ${meta.className}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  );
}

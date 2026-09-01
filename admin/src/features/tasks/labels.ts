export const PROTOCOL_LABELS: Record<string, string> = {
  openai_chat: "OpenAI Chat",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
};

export const REPLY_STRATEGY_LABELS: Record<string, string> = {
  human: "人工",
  llm: "LLM",
  human_fallback_llm: "人工兜底 LLM",
};

export const DELIVERY_MODE_LABELS: Record<string, string> = {
  web: "Web",
  im: "IM",
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  created: "任务创建",
  delivered: "已投递",
  reply_submitted: "回复提交",
  reply_rejected_late: "晚到拒绝",
  fallback: "兜底触发",
  stream: "流式输出",
  completed: "任务完成",
  failed: "任务失败",
  cancelled: "任务取消",
  timed_out: "人工超时",
};

export const ACTOR_TYPE_LABELS: Record<string, string> = {
  system: "系统",
  user: "用户",
  im: "IM",
  upstream: "上游",
  caller: "调用方",
};

export const DRAFT_SOURCE_LABELS: Record<string, string> = {
  manual: "手动",
  llm: "LLM",
};

export const DRAFT_STATE_LABELS: Record<string, string> = {
  editing: "编辑中",
  submitted: "已提交",
  discarded: "已丢弃",
};

export const STATE_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: "waiting_human", label: "等待人工" },
  { value: "received", label: "已接收" },
  { value: "forwarding_llm", label: "LLM 处理" },
  { value: "response_ready", label: "待输出" },
  { value: "responding", label: "输出中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "timed_out", label: "超时" },
  { value: "cancelled", label: "已取消" },
];

const TERMINAL_TASK_STATES = new Set(["completed", "failed", "timed_out", "cancelled"]);

/** 终态任务不再展示人工截止倒计时（截止语义只属于等待中的任务）。 */
export function isTerminalTaskState(state: string): boolean {
  return TERMINAL_TASK_STATES.has(state);
}

export function formatDateTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

export function formatDeadline(value: string | null): string {
  if (!value) return "-";
  const deadline = new Date(value).getTime();
  const now = Date.now();
  const remaining = deadline - now;
  if (remaining <= 0) return "已超时";
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  return `剩 ${minutes} 分 ${seconds} 秒`;
}

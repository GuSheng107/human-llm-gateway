import { ApiError } from "../api/client";
import { notify } from "../components/feedback/Toast";

// 后端统一错误结构中的 action -> 面向用户的操作建议。
const ACTION_HINTS: Record<string, string> = {
  relogin: "请重新登录后再试",
  retry: "请稍后重试",
  fix_input: "请检查输入内容后再试",
  view_logs: "请在日志中查看详情",
};

/**
 * 把任意异常转换为面向用户的错误文案：
 * - ApiError：使用后端的中文 message，并按 action 追加操作建议；
 * - TypeError（fetch 网络失败）：提示网络问题，避免展示英文原始信息；
 * - 其他：兜底文案，绝不把技术堆栈透给用户。
 */
export function friendlyErrorMessage(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) {
    const message = caught.message || fallback;
    const hint = ACTION_HINTS[caught.action];
    return hint ? `${message}，${hint}` : message;
  }
  if (caught instanceof TypeError) {
    return "网络连接失败，请检查网络后重试";
  }
  return fallback;
}

/** 统一错误提示入口：所有失败操作使用 error 样式和友好文案。 */
export function notifyError(caught: unknown, fallback: string) {
  notify(friendlyErrorMessage(caught, fallback), "error");
}

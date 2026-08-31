import { notify } from "../components/feedback/Toast";

export type CopyResult = {
  ok: boolean;
  reason: "clipboard" | "exec-command" | "empty" | "unavailable";
};

export async function copyText(text: string, label?: string): Promise<CopyResult> {
  if (!text) {
    notify("没有可复制的内容", "error");
    return { ok: false, reason: "empty" };
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      notify(`已复制${label ? ` ${label}` : ""}`, "success");
      return { ok: true, reason: "clipboard" };
    }
  } catch {
    // 非安全上下文或权限被拒绝时继续使用 textarea 兼容路径。
  }

  let textarea: HTMLTextAreaElement | null = null;
  try {
    textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const copied = document.execCommand("copy");
    if (copied) {
      notify(`已复制${label ? ` ${label}` : ""}`, "success");
      return { ok: true, reason: "exec-command" };
    }
  } catch {
    // 统一走失败提示。
  } finally {
    textarea?.remove();
  }

  notify("复制失败，请手动选择", "error");
  return { ok: false, reason: "unavailable" };
}

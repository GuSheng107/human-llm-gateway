import type { AssistantPageContext, AssistantUnsavedEdit } from "../../types/gateway";

/**
 * 编辑器桥：任务回复编辑器（ReplyEditor）与全局助手面板之间的跨 feature 通信。
 *
 * - 编辑器挂载时 register：上报未提交草稿（getDraft）与任务资源字段
 *   （getResource）；卸载时注销。
 * - 助手面板发送消息时经 buildContextSnapshot 读取；
 *   「插入回复」经 apply 覆盖编辑器内容（用户已确认覆盖）。
 */

export interface EditBridge {
  /** 当前未提交草稿（reasoning/final_text/tool_calls）。 */
  getDraft: () => AssistantUnsavedEdit | null;
  /** 任务资源白名单字段（task_id/state 等，来自编辑器所属任务）。 */
  getResource: () => Record<string, string>;
  /** 覆盖编辑器草稿（用户确认后调用）。 */
  apply: (draft: AssistantUnsavedEdit) => void;
}

let bridge: EditBridge | null = null;

export function registerEditBridge(next: EditBridge | null): void {
  bridge = next;
}

export function currentEditBridge(): EditBridge | null {
  return bridge;
}

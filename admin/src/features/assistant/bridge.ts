import type { AssistantPageContext, AssistantUnsavedEdit } from "../../types/gateway";

/**
 * 编辑器桥（只读）：回复工作台与全局助手面板之间的跨 feature 通信。
 *
 * - 编辑器挂载时 register：上报未提交草稿（getDraft）与任务资源字段
 *   （getResource）；卸载时注销。
 * - 助手面板发送消息时经 buildContextSnapshot 读取，作为脱敏上下文参考。
 * - 只读边界：助手不得写入草稿。原 apply（覆盖编辑器内容）已按
 *   docs/WORKBENCH_TOOL_CALL_LOG_REFACTOR_PLAN.md §10.3 移除——小助手
 *   只做问答与建议，参数建议由用户复制回编辑器。
 */

export interface EditBridge {
  /** 当前未提交草稿（reasoning/final_text/tool_calls），只读参考。 */
  getDraft: () => AssistantUnsavedEdit | null;
  /** 任务资源白名单字段（task_id/state 等，来自编辑器所属任务）。 */
  getResource: () => Record<string, string>;
}

let bridge: EditBridge | null = null;

export function registerEditBridge(next: EditBridge | null): void {
  bridge = next;
}

export function currentEditBridge(): EditBridge | null {
  return bridge;
}

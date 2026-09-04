import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  generateDraft,
  getConversation,
  getTask,
  saveDraft,
  submitReply,
  updateDraft,
  type ConversationPage,
  type DraftGenerateMode,
} from "../../api/tasks";
import { listLlmConfigs } from "../../api/llmConfigs";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { friendlyErrorMessage } from "../../utils/notify";
import type { LlmConfig, ReplyDraft, TaskDetail, ToolCall } from "../../types/gateway";
import { registerEditBridge } from "../assistant/bridge";
import { PROTOCOL_LABELS, formatDeadline, isTerminalTaskState } from "./labels";

function isEmptyDraft(draft: ReplyDraft): boolean {
  return Boolean(
    !draft.reasoning?.trim() && !draft.final_text?.trim() && draft.tool_calls.length === 0,
  );
}

// ---------------------------------------------------------------------------
// 工具调用编辑器（与后端 ReplyDraft.tool_calls 对齐）
// ---------------------------------------------------------------------------

interface ToolCallEditor {
  id: string;
  name: string;
  argumentsText: string;
}

function toEditors(draft: ReplyDraft | null): ToolCallEditor[] {
  // 工具调用不是必须的：无调用时返回空数组（编辑器允许 0 条）。
  if (!draft || draft.tool_calls.length === 0) {
    return [];
  }
  return draft.tool_calls.map((call) => ({
    id: call.id,
    name: call.name,
    argumentsText: JSON.stringify(call.arguments, null, 2),
  }));
}

function nextCallId(existing: ToolCallEditor[]): string {
  let index = 1;
  const ids = new Set(existing.map((c) => c.id));
  while (ids.has(`call_${String(index).padStart(2, "0")}`)) index += 1;
  return `call_${String(index).padStart(2, "0")}`;
}

type BuildResult =
  | { ok: true; draft: ReplyDraft }
  | { ok: false; error: string };

function buildDraft(
  reasoning: string,
  toolCalls: ToolCallEditor[],
  finalText: string,
): BuildResult {
  const parsed: ToolCall[] = [];
  for (const editor of toolCalls) {
    // 整行留空视为未添加调用（允许 0 条工具调用直接提交）。
    const hasId = editor.id.trim().length > 0;
    const hasName = editor.name.trim().length > 0;
    const hasArguments = editor.argumentsText.trim().length > 0;
    if (!hasId && !hasName && !hasArguments) continue;
    if (!hasId || !hasName) {
      return { ok: false, error: "每个工具调用的 id 与 name 不能为空" };
    }
    let args: Record<string, unknown> = {};
    const text = editor.argumentsText.trim();
    if (text) {
      try {
        const value = JSON.parse(text);
        if (typeof value !== "object" || value === null || Array.isArray(value)) {
          return { ok: false, error: `tool ${editor.id} 的 arguments 必须是 JSON 对象` };
        }
        args = value as Record<string, unknown>;
      } catch {
        return { ok: false, error: `tool ${editor.id} 的 arguments 不是合法 JSON` };
      }
    }
    parsed.push({ id: editor.id.trim(), name: editor.name.trim(), arguments: args });
  }
  return {
    ok: true,
    draft: {
      reasoning: reasoning.trim() || null,
      tool_calls: parsed,
      final_text: finalText.trim() || null,
    },
  };
}

// ---------------------------------------------------------------------------
// 编辑器（工作台回复弹窗内嵌）
// ---------------------------------------------------------------------------

type TabKey = "reasoning" | "final" | "tools";

/** 工具风险警告的 sessionStorage 记忆键：本次登录只弹一次。 */
const TOOL_WARN_KEY = "hlg_tool_call_warned";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "reasoning", label: "思考链", icon: "list" },
  { key: "final", label: "正式回复", icon: "reply" },
  { key: "tools", label: "工具调用", icon: "code" },
];

function deadlineTone(deadlineAt: string | null): string {
  if (!deadlineAt) return "text-slate-400";
  const remaining = new Date(deadlineAt).getTime() - Date.now();
  if (remaining <= 0) return "text-red-600";
  if (remaining < 5 * 60_000) return "text-red-500";
  if (remaining < 30 * 60_000) return "text-amber-600";
  return "text-slate-400";
}

export interface TaskEditorProps {
  taskId: string;
  /** 提交成功后回调（工作台用于关闭弹窗并刷新收件箱）。 */
  onSubmitted?: (taskId: string) => void;
}

export function TaskEditor({ taskId, onSubmitted }: TaskEditorProps) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [tab, setTab] = useState<TabKey>("final");
  const [toolWarnOpen, setToolWarnOpen] = useState(false);

  const [reasoning, setReasoning] = useState("");
  const [finalText, setFinalText] = useState("");
  const [toolCalls, setToolCalls] = useState<ToolCallEditor[]>([]);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [preview, setPreview] = useState<ReplyDraft | null>(null);
  const [showGenerate, setShowGenerate] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [generateMode, setGenerateMode] = useState<DraftGenerateMode>("both");
  const [guidance, setGuidance] = useState("");
  const [conversation, setConversation] = useState<ConversationPage | null>(null);
  const [excludedIndices, setExcludedIndices] = useState<number[]>([]);
  // 草稿乐观锁版本（服务端 DraftView.version）。
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  // 截止时间每秒重渲染（formatDeadline 是"剩余时间"语义）。
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    setError("");
    try {
      setTask(await getTask(taskId));
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "加载失败"));
    }
  }, [taskId]);

  useEffect(() => void load(), [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!task?.can_edit) return;
    listLlmConfigs(1)
      .then((page) => setLlmConfigs(page.items.filter((cfg) => cfg.is_enabled)))
      .catch(() => setLlmConfigs([]));
  }, [task?.can_edit]);

  // 任务首次加载时回填已有草稿 / 已接受回复。
  const appliedTaskRef = useRef<string | null>(null);
  const applyDraft = useCallback((draft: ReplyDraft) => {
    setReasoning(draft.reasoning ?? "");
    setFinalText(draft.final_text ?? "");
    setToolCalls(toEditors(draft));
  }, []);
  useEffect(() => {
    if (!task || appliedTaskRef.current === task.id) return;
    appliedTaskRef.current = task.id;
    const activeDraft = task.active_draft_id
      ? task.drafts.find((d) => d.id === task.active_draft_id && d.state === "editing")
      : null;
    const initial = activeDraft ?? task.result_draft;
    if (initial) applyDraft(initial);
    // 同步草稿版本（乐观锁）与 activeDraftId
    setActiveDraftId(activeDraft ? activeDraft.id : null);
    setDraftVersion(activeDraft ? activeDraft.version : null);
  }, [task, applyDraft]);

  const liveDraft = useMemo(() => {
    const result = buildDraft(reasoning, toolCalls, finalText);
    return result.ok ? result.draft : null;
  }, [reasoning, toolCalls, finalText]);

  // 编辑器桥：全局助手读取未提交草稿与覆盖写入（与工作台共享同一契约）。
  const liveDraftRef = useRef<ReplyDraft | null>(liveDraft);
  liveDraftRef.current = liveDraft;
  useEffect(() => {
    if (!task) return;
    registerEditBridge({
      getDraft: () => {
        const draft = liveDraftRef.current;
        if (!draft) return null;
        return {
          reasoning: draft.reasoning,
          final_text: draft.final_text,
          tool_calls: draft.tool_calls,
        };
      },
      getResource: () => ({
        task_id: task.id,
        public_id: task.public_id,
        state: task.state,
        model: task.fake_model_name,
        protocol: task.protocol,
        strategy: task.reply_strategy,
        delivery: task.delivery_mode,
      }),
      apply: (draft) => {
        applyDraft(draft);
        notify("已覆盖编辑器内容");
      },
    });
    return () => registerEditBridge(null);
  }, [task, applyDraft]);

  const doSave = useCallback(async () => {
    if (!task || !task.can_edit || saving) return;
    const result = buildDraft(reasoning, toolCalls, finalText);
    if (!result.ok) {
      notify(result.error, "error");
      return;
    }
    setSaving(true);
    try {
      if (activeDraftId) {
        const updated = await updateDraft(
          task.id,
          activeDraftId,
          result.draft,
          draftVersion ?? 1,
        );
        setActiveDraftId(updated.id);
        setDraftVersion(updated.version);
      } else {
        const created = await saveDraft(task.id, result.draft);
        setActiveDraftId(created.id);
        setDraftVersion(created.version);
      }
      notify("草稿已保存", "success");
      void load();
    } catch (caught) {
      const message = friendlyErrorMessage(caught, "保存失败");
      notify(message, "error");
      if (message.includes("草稿已被其他端修改")) {
        void load();
      }
    } finally {
      setSaving(false);
    }
  }, [task, saving, reasoning, toolCalls, finalText, activeDraftId, draftVersion, load]);

  const doSubmit = useCallback(() => {
    if (!task || !task.can_edit) return;
    const result = buildDraft(reasoning, toolCalls, finalText);
    if (!result.ok) {
      notify(result.error, "error");
      return;
    }
    if (isEmptyDraft(result.draft)) {
      notify("回复内容不能为空", "error");
      return;
    }
    setPreview(result.draft);
  }, [task, reasoning, toolCalls, finalText]);

  const confirmSubmit = async () => {
    if (!preview || !task) return;
    setSubmitting(true);
    try {
      await submitReply(task.id, preview, activeDraftId ?? undefined);
      notify("回复已提交", "success");
      onSubmitted?.(task.id);
    } catch (caught) {
      const message = friendlyErrorMessage(caught, "提交失败");
      notify(message, "error");
      // 任务被其他来源（IM/超时/fallback）抢先：清空预览并刷新任务详情
      if (message.includes("该任务已被其他提交接管") || message.includes("任务已结束")) {
        setPreview(null);
        void load();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const openGenerate = useCallback(() => {
    if (!task) return;
    setShowGenerate(true);
    setGenerateError("");
    setExcludedIndices([]);
    setGuidance("");
    setConversation(null);
    getConversation(task.id)
      .then(setConversation)
      .catch(() => setConversation(null));
  }, [task]);

  const handleGenerate = async (llmConfigId: number) => {
    if (!task) return;
    setGenerating(true);
    setGenerateError("");
    try {
      const draft = await generateDraft(task.id, {
        llm_config_id: llmConfigId,
        mode: generateMode,
        exclude_context_indices: excludedIndices.length ? excludedIndices : undefined,
        reasoning_seed:
          generateMode === "reply" && reasoning.trim() ? reasoning.trim() : undefined,
        guidance: guidance.trim() ? guidance.trim() : undefined,
      });
      setActiveDraftId(draft.id);
      setDraftVersion(draft.version);
      applyDraft(draft);
      setShowGenerate(false);
      notify(
        generateMode === "reasoning"
          ? "已生成思考链，请继续编辑"
          : generateMode === "reply"
            ? "已生成回复，请继续编辑"
            : "已生成草稿，请继续编辑",
        "success",
      );
      void load();
    } catch (caught) {
      setGenerateError(friendlyErrorMessage(caught, "生成失败"));
    } finally {
      setGenerating(false);
    }
  };

  // 键盘快捷键：Ctrl/Cmd+S 保存草稿，Ctrl/Cmd+Enter 提交。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        void doSave();
      } else if (event.key === "Enter") {
        event.preventDefault();
        doSubmit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doSave, doSubmit]);

  const updateCall = (index: number, patch: Partial<ToolCallEditor>) => {
    setToolCalls((prev) => prev.map((call, i) => (i === index ? { ...call, ...patch } : call)));
  };

  const addCall = () => {
    setToolCalls((prev) => [...prev, { id: nextCallId(prev), name: "", argumentsText: "{}" }]);
  };

  const removeCall = (index: number) => {
    // 允许删到 0 条：工具调用不是必须的。
    setToolCalls((prev) => prev.filter((_, i) => i !== index));
  };

  /** 工具相关操作前的风险警告：本次登录只弹一次（sessionStorage 记忆）。 */
  const requireToolWarn = () => {
    if (sessionStorage.getItem(TOOL_WARN_KEY)) return;
    setToolWarnOpen(true);
  };

  const switchTab = (next: TabKey) => {
    if (next === "tools") requireToolWarn();
    setTab(next);
  };

  /** 声明工具勾选：勾选新增一条调用，取消移除同名调用。 */
  const toggleTool = (name: string, checked: boolean) => {
    if (checked) requireToolWarn();
    setToolCalls((prev) => {
      if (checked) {
        if (prev.some((c) => c.name === name)) return prev;
        return [...prev, { id: nextCallId(prev), name, argumentsText: "{}" }];
      }
      return prev.filter((c) => c.name !== name);
    });
    if (checked) setTab("tools");
  };

  const dismissToolWarn = () => {
    sessionStorage.setItem(TOOL_WARN_KEY, "1");
    setToolWarnOpen(false);
  };

  if (error && !task) {
    return <ErrorBanner message={error} />;
  }

  if (!task) {
    return (
      <div className="grid min-h-64 place-items-center text-slate-400">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
      </div>
    );
  }

  const canEdit = task.can_edit;
  const declaredToolNames = new Set(task.tool_names);
  // 提前提示未声明名称（后端会拒绝提交）。
  const undeclaredCallNames = toolCalls
    .map((call) => call.name.trim())
    .filter((name) => name && !declaredToolNames.has(name));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2.5 shadow-card">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="font-mono font-medium text-slate-700">#{task.public_id}</span>
          <StatusBadge status={task.state} />
          <span className="text-slate-400">{PROTOCOL_LABELS[task.protocol] ?? task.protocol}</span>
          {task.human_deadline_at && !isTerminalTaskState(task.state) && (
            <span className={`font-medium ${deadlineTone(task.human_deadline_at)}`}>
              {formatDeadline(task.human_deadline_at)}
            </span>
          )}
          {task.has_tools && (
            <span className="inline-flex items-center gap-1 text-primary">
              <Icon name="code" className="h-3.5 w-3.5" />
              含工具
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {llmConfigs.length > 0 && canEdit && (
            <Button variant="ghost" onClick={openGenerate}>
              <Icon name="gateway" className="h-4 w-4" />
              生成草稿
            </Button>
          )}
          <Button variant="ghost" onClick={() => void doSave()} loading={saving} disabled={!canEdit}>
            保存草稿
          </Button>
          <Button onClick={doSubmit} disabled={!canEdit}>
            <Icon name="check" className="h-4 w-4" />
            提交回复
          </Button>
        </div>
      </div>

      {!canEdit && (
        <ErrorBanner message="当前任务状态或归属不允许编辑回复。" />
      )}

      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[minmax(0,1.6fr)_minmax(220px,0.8fr)]">
        {/* 左栏：编辑器（tab 切换，内容互不丢失） */}
        <section className="space-y-4">
          <Card>
            <div className="flex gap-1 border-b border-slate-100 px-4 pt-3">
              {TABS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => switchTab(item.key)}
                  className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition ${
                    tab === item.key
                      ? "border-primary text-primary"
                      : "border-transparent text-slate-400 hover:text-slate-600"
                  }`}
                >
                  <Icon name={item.icon} className="h-3.5 w-3.5" />
                  {item.label}
                  {item.key === "tools" && toolCalls.length > 0 && (
                    <span className="rounded-full bg-primary/10 px-1.5 text-[10px] text-primary">
                      {toolCalls.length}
                    </span>
                  )}
                </button>
              ))}
            </div>

            <div className="p-4">
              {tab === "reasoning" && (
                <div>
                  <p className="mb-2 text-xs text-slate-400">
                    人工推理过程，不会作为最终回复输出
                  </p>
                  <textarea
                    value={reasoning}
                    onChange={(event) => setReasoning(event.target.value)}
                    disabled={!canEdit}
                    className="field-input min-h-[320px] font-mono text-xs"
                    placeholder="::: reasoning 围栏块的等价内容"
                  />
                </div>
              )}

              {tab === "final" && (
                <div>
                  <p className="mb-2 text-xs text-slate-400">
                    提交后作为最终回复返回
                  </p>
                  <textarea
                    value={finalText}
                    onChange={(event) => setFinalText(event.target.value)}
                    disabled={!canEdit}
                    className="field-input min-h-[320px] text-sm"
                    placeholder="面向调用方的最终回复内容"
                  />
                </div>
              )}

              {tab === "tools" && (
                <div className="space-y-3">
                  <p className="text-xs text-slate-400">
                    工具调用由调用方声明并自行执行，本网关仅伪造输出转发、不执行也不担保结果；
                    名称必须命中调用方声明的工具，工具调用不是必须的。
                  </p>
                  {task.tool_names.length === 0 && (
                    <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                      本次请求未声明任何工具，不能提交工具调用。
                    </p>
                  )}
                  {undeclaredCallNames.length > 0 && (
                    <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                      以下名称未在调用方声明的工具内，提交将被拒绝：
                      {undeclaredCallNames.join("、")}
                    </p>
                  )}
                  {toolCalls.length === 0 && (
                    <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400">
                      未添加工具调用；仅填写正式回复即可提交
                    </p>
                  )}
                  {toolCalls.map((call, index) => (
                    <div
                      key={index}
                      className="space-y-2 rounded-lg border border-slate-200 p-3"
                    >
                      <div className="flex gap-2">
                        <input
                          value={call.id}
                          disabled={!canEdit}
                          onChange={(event) => updateCall(index, { id: event.target.value })}
                          className="field-input w-32 font-mono"
                          placeholder="call_01"
                        />
                        <input
                          value={call.name}
                          disabled={!canEdit}
                          onChange={(event) => updateCall(index, { name: event.target.value })}
                          className="field-input min-w-0 flex-1"
                          placeholder="工具名称"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => removeCall(index)}
                          aria-label="删除工具调用"
                        >
                          <Icon name="close" className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      <textarea
                        value={call.argumentsText}
                        disabled={!canEdit}
                        onChange={(event) =>
                          updateCall(index, { argumentsText: event.target.value })
                        }
                        className="field-input min-h-[72px] font-mono text-[11px]"
                        placeholder='{"key":"value"}'
                      />
                    </div>
                  ))}
                  {canEdit && (
                    <Button type="button" variant="ghost" onClick={addCall}>
                      <Icon name="plus" className="h-3.5 w-3.5" />
                      添加工具调用
                    </Button>
                  )}
                </div>
              )}
            </div>
          </Card>
        </section>

        {/* 右栏：工具调用清单 + 调用方声明的工具 */}
        <aside className="space-y-4 md:sticky md:top-0 md:self-start">
          <Card>
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <span className="text-sm font-medium text-slate-700">工具调用</span>
              <span className="text-[11px] text-slate-400">{toolCalls.length} 条</span>
            </div>
            <div className="divide-y divide-slate-100">
              {toolCalls.map((call, index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => setTab("tools")}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-xs hover:bg-slate-50"
                >
                  <Icon name="code" className="h-3.5 w-3.5 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate font-mono text-slate-600">
                    {call.name || "(未命名)"}
                  </span>
                  {canEdit && (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label={`移除 ${call.name || "未命名调用"}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        removeCall(index);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.stopPropagation();
                          removeCall(index);
                        }
                      }}
                      className="shrink-0 text-slate-300 transition hover:text-red-500"
                    >
                      <Icon name="close" className="h-3.5 w-3.5" />
                    </span>
                  )}
                </button>
              ))}
              {toolCalls.length === 0 && (
                <p className="px-4 py-4 text-xs text-slate-400">尚未添加工具调用</p>
              )}
            </div>
          </Card>

          <Card>
            <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
              调用方声明的工具
            </div>
            <div className="max-h-72 overflow-auto divide-y divide-slate-100">
              {task.tool_names.map((name) => {
                const checked = toolCalls.some((call) => call.name === name);
                return (
                  <label
                    key={name}
                    className="flex cursor-pointer items-start gap-2.5 px-4 py-2.5 hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!canEdit}
                      onChange={(event) => toggleTool(name, event.target.checked)}
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-slate-700">
                      {name}
                    </span>
                  </label>
                );
              })}
              {task.tool_names.length === 0 && (
                <p className="px-4 py-4 text-xs text-slate-400">本次请求未声明工具</p>
              )}
            </div>
            <p className="border-t border-slate-100 px-4 py-2.5 text-[11px] leading-relaxed text-slate-400">
              勾选后自动生成调用条目；工具由调用方自行执行，本网关不执行、不担保结果。
            </p>
          </Card>
        </aside>
      </div>

      {toolWarnOpen && (
        <Modal
          title="工具调用风险提示"
          onClose={() => {
            // 关闭即视为已知悉：记录本次登录不再弹出。
            dismissToolWarn();
          }}
          width="max-w-lg"
        >
          <div className="space-y-4 p-6 text-sm text-slate-600">
            <p>
              工具调用由调用方在请求中声明并<span className="font-medium">自行执行</span>，
              本网关仅转发伪造的 tool call 输出，<span className="font-medium">不执行任何工具、不担保执行结果</span>。
            </p>
            <p className="text-amber-700">
              慎重使用，由此产生的后果由使用者自行承担。本次登录内不再重复提示。
            </p>
            <div className="flex justify-end border-t border-slate-100 pt-4">
              <Button onClick={dismissToolWarn}>
                <Icon name="check" className="h-4 w-4" />
                我已知晓
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {showGenerate && (
        <Modal
          title="调用 LLM 生成草稿"
          description="选择同协议 LLM 与生成模式；可填写引导提示词、勾选参与生成的上下文消息。"
          onClose={() => setShowGenerate(false)}
          width="max-w-3xl"
        >
          <div className="max-h-[82vh] space-y-5 overflow-y-auto p-6">
            {generateError && <ErrorBanner message={generateError} />}

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold text-slate-700">生成模式</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {([
                  { value: "reasoning", label: "只生成思考链", hint: "上游只输出推理过程" },
                  { value: "reply", label: "只生成回复", hint: "可基于你写的思考链" },
                  { value: "both", label: "两者都生成", hint: "默认行为" },
                ] as const).map((opt) => (
                  <label
                    key={opt.value}
                    className={`flex cursor-pointer flex-col gap-1 rounded-lg border px-3 py-2 text-xs transition ${
                      generateMode === opt.value
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-slate-200 hover:border-primary/40"
                    }`}
                  >
                    <span className="flex items-center gap-2 font-medium">
                      <input
                        type="radio"
                        name="generate-mode"
                        value={opt.value}
                        checked={generateMode === opt.value}
                        onChange={() => setGenerateMode(opt.value as DraftGenerateMode)}
                        className="h-3 w-3"
                      />
                      {opt.label}
                    </span>
                    <span className="text-[11px] text-slate-400">{opt.hint}</span>
                  </label>
                ))}
              </div>
              {generateMode === "reply" && reasoning.trim() && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                  将以你编辑器内当前的思考链（{(reasoning.trim().length).toLocaleString()} 字）作为生成依据。
                </p>
              )}
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold text-slate-700">引导提示词（可选）</legend>
              <textarea
                value={guidance}
                onChange={(event) => setGuidance(event.target.value)}
                maxLength={4000}
                className="field-input min-h-[72px] text-xs"
                placeholder="例如：用简洁中文回复；工具参数中城市字段使用拼音；先总结再给结论…"
              />
              <p className="text-[11px] text-slate-400">
                作为系统指令注入上游请求，引导 LLM 按期望生成思考链 / 回复 / 工具调用参数。
              </p>
            </fieldset>

            <fieldset className="space-y-2">
              <div className="flex items-center justify-between">
                <legend className="text-xs font-semibold text-slate-700">参与生成的上下文</legend>
                <div className="flex gap-2 text-[11px] text-primary">
                  <button
                    type="button"
                    className="hover:underline"
                    onClick={() => setExcludedIndices([])}
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    className="hover:underline"
                    onClick={() => {
                      const all = (conversation?.messages ?? [])
                        .map((m) => m.context_index)
                        .filter((i): i is number => i !== null);
                      setExcludedIndices(all);
                    }}
                  >
                    全部排除
                  </button>
                </div>
              </div>
              {conversation === null && (
                <p className="rounded-md border border-dashed border-slate-200 px-3 py-2 text-center text-xs text-slate-400">
                  加载上下文中…
                </p>
              )}
              {conversation !== null && conversation.messages.length === 0 && (
                <p className="rounded-md border border-dashed border-slate-200 px-3 py-2 text-center text-xs text-slate-400">
                  没有可用的上下文消息
                </p>
              )}
              {conversation !== null && conversation.messages.length > 0 && (
                <div className="max-h-72 divide-y divide-slate-100 overflow-auto rounded-lg border border-slate-200">
                  {conversation.messages.map((message) => {
                    const isSystem = message.context_index === null;
                    const checked = !excludedIndices.includes(message.context_index ?? -1);
                    return (
                      <label
                        key={message.index}
                        className="flex cursor-pointer items-start gap-2 px-3 py-2 text-xs hover:bg-slate-50"
                      >
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={isSystem ? true : checked}
                          disabled={isSystem}
                          onChange={(event) => {
                            const ctxIdx = message.context_index;
                            if (ctxIdx === null) return;
                            setExcludedIndices((prev) =>
                              event.target.checked
                                ? prev.filter((i) => i !== ctxIdx)
                                : Array.from(new Set([...prev, ctxIdx])),
                            );
                          }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="mb-1 flex items-center gap-2 text-[11px] text-slate-500">
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium capitalize">
                              {message.role}
                            </span>
                            <span>{message.length.toLocaleString()} 字</span>
                            {isSystem && (
                              <span className="text-amber-600">系统指令（始终参与）</span>
                            )}
                          </span>
                          <span className="line-clamp-2 whitespace-pre-wrap break-words font-mono text-[11px] text-slate-600">
                            {message.preview || "(空)"}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
              <p className="text-[11px] text-slate-400">
                取消勾选的消息不会送入上游 LLM；系统指令始终参与生成。
              </p>
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold text-slate-700">LLM 配置</legend>
              {llmConfigs.length === 0 ? (
                <p className="text-slate-400">暂无启用的 LLM 配置</p>
              ) : (
                <div className="space-y-2">
                  {llmConfigs.map((cfg) => (
                    <button
                      key={cfg.id}
                      disabled={generating}
                      onClick={() => void handleGenerate(Number(cfg.id))}
                      className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-left hover:border-primary disabled:opacity-50"
                    >
                      <span>
                        <span className="block font-medium text-slate-700">{cfg.name}</span>
                        <span className="text-xs text-slate-400">
                          {PROTOCOL_LABELS[cfg.protocol] ?? cfg.protocol} · {cfg.real_model}
                        </span>
                      </span>
                      <span className="text-xs text-primary">
                        {generating ? "生成中…" : "生成"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </fieldset>

            <p className="text-xs text-slate-400">不兼容字段会拒绝生成。</p>
          </div>
        </Modal>
      )}

      {preview && (
        <Modal
          title="确认提交回复"
          description="提交后不可修改"
          onClose={() => setPreview(null)}
        >
          <div className="space-y-4 p-6 text-xs">
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">结构化预览</h3>
              <div className="space-y-2 rounded-lg border border-slate-100 bg-slate-50 p-4">
                {preview.reasoning && (
                  <div>
                    <span className="text-slate-400">思考</span>
                    <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-600">
                      {preview.reasoning}
                    </pre>
                  </div>
                )}
                {preview.tool_calls.length > 0 && (
                  <div>
                    <span className="text-slate-400">工具调用（{preview.tool_calls.length}）</span>
                    <ul className="mt-1 space-y-1">
                      {preview.tool_calls.map((call) => (
                        <li key={call.id} className="font-mono text-[11px] text-slate-600">
                          {call.id} · {call.name} · {JSON.stringify(call.arguments)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <span className="text-slate-400">最终文本</span>
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-600">
                    {preview.final_text}
                  </pre>
                </div>
              </div>
            </section>
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <Button variant="ghost" onClick={() => setPreview(null)}>
                返回编辑
              </Button>
              <Button onClick={() => void confirmSubmit()} loading={submitting}>
                <Icon name="check" className="h-4 w-4" />
                确认提交
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { generateDraft, getTask, getTaskRawRequest, saveDraft, submitReply, updateDraft } from "../../api/tasks";
import { listTools, type ToolItem } from "../../api/tools";
import { listLlmConfigs } from "../../api/llmConfigs";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import type { LlmConfig, ReplyDraft, TaskDetail, TaskEvent, ToolCall } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import { registerEditBridge } from "../assistant/bridge";
import { isEmptyDraft, parseReply, serializeReply } from "./dsl";
import {
  EVENT_TYPE_LABELS,
  PROTOCOL_LABELS,
  formatDateTime,
  formatDeadline,
  isTerminalTaskState,
} from "./labels";

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

function defaultToolArguments(tool: ToolItem): string {
  const arguments_: Record<string, string> = {};
  Object.keys(tool.arguments_schema?.properties ?? {}).forEach((name) => {
    arguments_[name] = "";
  });
  return JSON.stringify(arguments_, null, 2);
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
// 页面
// ---------------------------------------------------------------------------

type TabKey = "reasoning" | "final" | "tools";

// 提示词超过该字数时提供「展开全部」（默认折叠高度约可见 800 字）。
const PROMPT_COLLAPSE_THRESHOLD = 800;

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

function EventRow({ event }: { event: TaskEvent }) {
  return (
    <li className="flex gap-2.5 py-2">
      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] font-medium text-slate-700">
            {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
          </span>
          <span className="shrink-0 text-[10px] text-slate-400">
            {formatDateTime(event.created_at)}
          </span>
        </div>
      </div>
    </li>
  );
}

export interface TaskEditorProps {
  taskId: string;
  /** 提交成功后回调（工作台用于从列表移除并选中下一条）。 */
  onSubmitted?: (taskId: string) => void;
  /** 是否渲染 PageHeader/返回（独立页 true，工作台内嵌 false）。 */
  standalone?: boolean;
}

export function TaskEditor({ taskId, onSubmitted, standalone = true }: TaskEditorProps) {
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";

  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [tab, setTab] = useState<TabKey>("final");

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
  const [dslInput, setDslInput] = useState("");
  const [dslError, setDslError] = useState("");
  const [promptExpanded, setPromptExpanded] = useState(false);
  // 草稿乐观锁版本（服务端 DraftView.version）。
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  // 截止时间每秒重渲染（formatDeadline 是"剩余时间"语义）。
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    setError("");
    try {
      setTask(await getTask(taskId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    }
  }, [taskId]);

  useEffect(() => void load(), [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    listTools(1, true)
      .then((page) => setTools(page.items))
      .catch(() => setTools([]));
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

  const liveDsl = useMemo(() => (liveDraft ? serializeReply(liveDraft) : ""), [liveDraft]);

  // 原始请求按需加载：进入页面不传输、不解析、不格式化超大 JSON。
  const [rawRequestText, setRawRequestText] = useState("");
  const [rawLoading, setRawLoading] = useState(false);
  const loadRawRequest = useCallback(async () => {
    if (!task || rawLoading || rawRequestText) return;
    setRawLoading(true);
    try {
      const result = await getTaskRawRequest(task.id);
      setRawRequestText(
        result.raw_request ? JSON.stringify(result.raw_request, null, 2) : "(空)",
      );
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "加载原始请求失败");
    } finally {
      setRawLoading(false);
    }
  }, [task, rawLoading, rawRequestText]);
  useEffect(() => {
    setRawRequestText("");
  }, [task?.id]);

  // 编辑器桥：全局助手读取未提交草稿与覆盖写入（与独立页共享同一契约）。
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
      notify(result.error);
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
      notify("草稿已保存");
      void load();
  } catch (caught) {
      const message = caught instanceof Error ? caught.message : "保存失败";
      notify(message);
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
      notify(result.error);
      return;
    }
    if (isEmptyDraft(result.draft)) {
      notify("回复内容不能为空");
      return;
    }
    setPreview(result.draft);
  }, [task, reasoning, toolCalls, finalText]);

  const confirmSubmit = async () => {
    if (!preview || !task) return;
    setSubmitting(true);
    try {
      await submitReply(task.id, preview, activeDraftId ?? undefined);
      notify("回复已提交");
      if (onSubmitted) {
        onSubmitted(task.id);
      } else {
        navigate("/tasks", { replace: true });
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "提交失败";
      notify(message);
      // 任务被其他来源（IM/超时/fallback）抢先：清空预览并刷新任务详情
      if (message.includes("该任务已被其他提交接管") || message.includes("任务已结束")) {
        setPreview(null);
        void load();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleGenerate = async (llmConfigId: number) => {
    if (!task) return;
    setGenerating(true);
    setGenerateError("");
    try {
      const draft = await generateDraft(task.id, llmConfigId);
      setActiveDraftId(draft.id);
      setDraftVersion(draft.version);
      applyDraft(draft);
      setShowGenerate(false);
      notify("已生成草稿，请继续编辑");
      void load();
    } catch (caught) {
      setGenerateError(caught instanceof Error ? caught.message : "生成失败");
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

  /** 允许工具勾选：勾选新增一条调用，取消移除同名调用。 */
  const toggleTool = (tool: ToolItem, checked: boolean) => {
    setToolCalls((prev) => {
      if (checked) {
        if (prev.some((c) => c.name === tool.name)) return prev;
        return [
          ...prev,
          {
            id: nextCallId(prev),
            name: tool.name,
            argumentsText: defaultToolArguments(tool),
          },
        ];
      }
      return prev.filter((c) => c.name !== tool.name);
    });
    if (checked) setTab("tools");
  };

  const importDsl = () => {
    setDslError("");
    if (!dslInput.trim()) {
      setDslError("请粘贴 DSL 文本");
      return;
    }
    try {
      const parsed = parseReply(dslInput);
      applyDraft(parsed);
      notify("已从 DSL 导入");
    } catch (caught) {
      setDslError(caught instanceof Error ? caught.message : "DSL 解析失败");
    }
  };

  if (error && !task) {
    return (
      <div className="space-y-4">
        {standalone && <PageHeader title="回复任务" />}
        <ErrorBanner message={error} />
        {standalone && (
          <Button variant="ghost" onClick={() => navigate("/tasks")}>
            <Icon name="chevronLeft" className="h-4 w-4" />
            返回任务记录
          </Button>
        )}
      </div>
    );
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

  return (
    <div className={standalone ? "space-y-5" : "flex min-h-0 flex-1 flex-col gap-4"}>
      {standalone ? (
        <PageHeader
          title={`回复任务 #${task.public_id}`}
          actions={
            <>
              <Button variant="ghost" onClick={() => navigate("/tasks")}>
                <Icon name="chevronLeft" className="h-4 w-4" />
                返回
              </Button>
              {llmConfigs.length > 0 && canEdit && (
                <Button variant="ghost" onClick={() => setShowGenerate(true)}>
                  <Icon name="gateway" className="h-4 w-4" />
                  生成草稿
                </Button>
              )}
              <Button
                variant="ghost"
                onClick={() => void doSave()}
                loading={saving}
                disabled={!canEdit}
                title="Ctrl/Cmd + S"
              >
                保存草稿
              </Button>
              <Button onClick={doSubmit} disabled={!canEdit} title="Ctrl/Cmd + Enter">
                <Icon name="check" className="h-4 w-4" />
                提交回复
              </Button>
            </>
          }
        />
      ) : (
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
              <Button variant="ghost" onClick={() => setShowGenerate(true)}>
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
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white px-5 py-3 text-xs shadow-card">
        <StatusBadge status={task.state} />
        <span className="text-slate-400">
          {PROTOCOL_LABELS[task.protocol] ?? task.protocol}
        </span>
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
        <span className="ml-auto text-slate-400">
          {activeDraftId ? "已有活动草稿，保存将覆盖" : "尚无草稿，保存将新建"}
        </span>
        <span className="hidden text-slate-300 sm:inline">
          快捷键：Ctrl+S 保存 · Ctrl+Enter 提交
        </span>
      </section>

      {!canEdit && (
        <ErrorBanner message="当前任务状态或归属不允许编辑回复，可切换任务记录查看详情。" />
      )}

      <div
        className={
          standalone
            ? "grid gap-5 xl:grid-cols-[minmax(240px,0.9fr)_minmax(0,2.3fr)_minmax(230px,1fr)]"
            : "grid min-h-0 flex-1 gap-4 md:grid-cols-[minmax(0,1.6fr)_minmax(220px,0.8fr)]"
        }
      >
        {standalone && (
          <aside className="space-y-5 xl:sticky xl:top-24 xl:self-start">
          <Card>
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <span className="text-sm font-medium text-slate-700">提示词</span>
              <span className="flex items-center gap-1 text-[11px] text-slate-400">
                <span>{(task.prompt_text || "").length.toLocaleString()} 字</span>
                <button
                  type="button"
                  aria-label="复制提示词"
                  title="复制提示词"
                  onClick={() => void copyText(task.prompt_text || "", "提示词")}
                  className="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-primary"
                >
                  <Icon name="copy" className="h-3.5 w-3.5" />
                </button>
              </span>
            </div>
            <pre
              className={`overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-[11px] text-slate-600 ${
                promptExpanded ? "max-h-[70vh]" : "max-h-56"
              }`}
            >
              {task.prompt_text || "(空)"}
            </pre>
            {(task.prompt_text || "").length > PROMPT_COLLAPSE_THRESHOLD && (
              <button
                type="button"
                onClick={() => setPromptExpanded((value) => !value)}
                className="w-full border-t border-slate-100 py-2 text-xs text-primary transition hover:bg-slate-50"
              >
                {promptExpanded ? "收起" : "展开全部"}
              </button>
            )}
            {!task.is_owner && (
              <p className="px-4 pb-3 text-[11px] text-slate-400">非归属用户仅显示前 200 字</p>
            )}
          </Card>

          {task.tool_names.length > 0 && (
            <Card>
              <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
                任务声明的工具
              </div>
              <div className="flex flex-wrap gap-2 px-4 py-3">
                {task.tool_names.map((name) => (
                  <span
                    key={name}
                    className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 font-mono text-[11px] text-slate-600"
                  >
                    {name}
                  </span>
                ))}
              </div>
            </Card>
          )}

          {task.is_owner && (
            <Card>
              <div className="px-4 py-3">
                <details onToggle={(event) => {
                  if ((event.target as HTMLDetailsElement).open) void loadRawRequest();
                }}>
                  <summary className="cursor-pointer text-sm font-medium text-slate-700">
                    原始请求{rawLoading ? "（加载中…）" : ""}
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto rounded border border-slate-100 bg-slate-50 p-3 font-mono text-[11px] text-slate-600">
                    {rawRequestText || "（展开后加载）"}
                  </pre>
                </details>
              </div>
            </Card>
          )}

          <Card>
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <span className="text-sm font-medium text-slate-700">事件时间线</span>
              <span className="text-[11px] text-slate-400">共 {task.events_total} 条</span>
            </div>
            {task.events.length > 0 ? (
              <ul className="max-h-64 divide-y divide-slate-100 overflow-auto px-4">
                {task.events.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </ul>
            ) : (
              <p className="px-4 py-4 text-xs text-slate-400">暂无事件</p>
            )}
          </Card>
        </aside>
        )}

        {/* 中栏：编辑器（tab 切换，内容互不丢失） */}
        <section className="space-y-4">
          <Card>
            <div className="flex gap-1 border-b border-slate-100 px-4 pt-3">
              {TABS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
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
                    仅作为回复结构转发，系统不会执行；工具调用不是必须的，可从右侧「允许的工具」勾选自动填充
                  </p>
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

          <details className="rounded-lg border border-slate-200 bg-slate-50/60">
            <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold text-slate-700">
              DSL 双向同步（与 IM 共享同一结构）
            </summary>
            <div className="space-y-3 border-t border-slate-200 px-4 pb-4 pt-3">
              <div>
                <span className="block text-xs text-slate-400">当前编辑器序列化为 DSL</span>
                <pre className="mt-1 max-h-40 overflow-auto rounded border border-slate-200 bg-white p-3 font-mono text-[11px] text-slate-600">
                  {liveDsl || "(空)"}
                </pre>
              </div>
              <div>
                <span className="block text-xs text-slate-400">从 DSL 导入（粘贴后解析）</span>
                <textarea
                  value={dslInput}
                  onChange={(event) => setDslInput(event.target.value)}
                  className="field-input mt-1 min-h-[72px] font-mono text-[11px]"
                  placeholder="::: reasoning&#10;...&#10;:::"
                />
                {dslError && <p className="mt-1 text-xs text-red-500">{dslError}</p>}
                <Button type="button" variant="ghost" className="mt-2" onClick={importDsl}>
                  <Icon name="upload" className="h-3.5 w-3.5" />
                  解析并导入
                </Button>
              </div>
            </div>
          </details>
        </section>

        {/* 右栏：工具调用清单 + 允许的工具（sticky） */}
        <aside className="space-y-5 xl:sticky xl:top-24 xl:self-start">
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
              允许的工具
            </div>
            <div className="max-h-72 overflow-auto divide-y divide-slate-100">
              {tools.map((tool) => {
                const checked = toolCalls.some((call) => call.name === tool.name);
                const declared = declaredToolNames.has(tool.name);
                return (
                  <label
                    key={tool.id}
                    className="flex cursor-pointer items-start gap-2.5 px-4 py-2.5 hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!canEdit}
                      onChange={(event) => toggleTool(tool, event.target.checked)}
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate font-mono text-xs font-medium text-slate-700">
                          {tool.name}
                        </span>
                        {declared && (
                          <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            任务声明
                          </span>
                        )}
                      </span>
                      {tool.description && (
                        <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                          {tool.description}
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
              {tools.length === 0 && (
                <p className="px-4 py-4 text-xs text-slate-400">暂无启用的工具</p>
              )}
            </div>
          </Card>
        </aside>
      </div>

      {showGenerate && (
        <Modal
          title="调用 LLM 生成草稿"
          description="选择同协议 LLM，生成可编辑草稿。"
          onClose={() => setShowGenerate(false)}
        >
          <div className="space-y-4 p-6">
            {generateError && <ErrorBanner message={generateError} />}
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
            <p className="text-xs text-slate-400">
              不兼容字段会拒绝生成。
            </p>
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
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">IM DSL 等价文本</h3>
              <pre className="max-h-48 overflow-auto rounded-lg border border-slate-100 bg-slate-50 p-4 font-mono text-[11px] text-slate-600">
                {serializeReply(preview)}
              </pre>
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

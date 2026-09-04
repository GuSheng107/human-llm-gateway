import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  createAssistantSession,
  deleteAssistantSession,
  getAssistantSession,
  patchAssistantSession,
  sendAssistantMessage,
  streamAssistantMessage,
} from "../../api/assistant";
import { ApiError } from "../../api/client";
import { listLlmConfigs } from "../../api/llmConfigs";
import { MarkdownText } from "../../components/data-display/MarkdownText";
import { notify } from "../../components/feedback/Toast";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { friendlyErrorMessage, notifyError } from "../../utils/notify";
import type {
  AssistantMessage,
  AssistantSession,
  AssistantSessionUsage,
  LlmConfig,
} from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import { currentEditBridge } from "./bridge";
import { buildContextSnapshot, featureForRoute } from "./contextRegistry";
import { useAssistant } from "./AssistantContext";

const CONTEXT_FEATURE_LABELS: Record<string, string> = {
  console: "控制台",
  task_list: "任务记录",
  task_detail: "任务回复",
  replies: "回复工作台",
  api_keys: "API 管理",
  llm_configs: "LLM 管理",
  connections: "连接 IM",
  models: "模型广场",
  tools: "工具沙箱",
  logs: "日志查询",
  invitations: "邀请码",
  users: "用户管理",
  account: "账号设置",
  adminConnections: "IM 连接监管",
};

/** 默认 LLM 偏好持久化 key（新建会话 / 直接发消息时使用）。 */
const DEFAULT_LLM_KEY = "hlg_assistant_default_llm";
/** 上下文使用比例 >= 此值时进度条转红。 */
const USAGE_WARN_RATIO = 0.8;

const WELCOME_CARDS: { title: string; prompt: string }[] = [
  {
    title: "查看当前页面",
    prompt: "概括当前页面",
  },
  {
    title: "润色任务回复",
    prompt: "润色当前任务回复",
  },
  {
    title: "排查连接错误",
    prompt: "排查最近的连接错误",
  },
];

/** 流式阶段——用于动态状态文案与气泡。 */
type StreamStage = "idle" | "compressing" | "thinking" | "replying";

interface RenameTarget {
  id: string;
  title: string;
}

export function AssistantPanel() {
  const { user } = useAuth();
  const { open, setOpen, sessions, activeSessionId, setActiveSessionId, refreshSessions } =
    useAssistant();
  const location = useLocation();
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [usage, setUsage] = useState<AssistantSessionUsage | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamStage, setStreamStage] = useState<StreamStage>("idle");
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [preferredConfigId, setPreferredConfigId] = useState<string>(() =>
    localStorage.getItem(DEFAULT_LLM_KEY) ?? "",
  );
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [insertPreview, setInsertPreview] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const sessionMenuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  const bridge = currentEditBridge();
  const feature = featureForRoute(location.pathname);

  /** 打开面板时强制刷新 LLM 配置与会话列表——配置是异步可变的。 */
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    listLlmConfigs(1)
      .then((page) => {
        if (cancelled) return;
        setLlmConfigs(page.items.filter((cfg) => cfg.is_enabled));
      })
      .catch(() => {
        if (cancelled) return;
        setLlmConfigs([]);
      });
    void refreshSessions();
    return () => {
      cancelled = true;
    };
  }, [open, refreshSessions]);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      setUsage(null);
      return;
    }
    let cancelled = false;
    getAssistantSession(activeSessionId)
      .then((detail) => {
        if (cancelled) return;
        setMessages(detail.messages);
        setUsage(detail.usage ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setMessages([]);
        setUsage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, open, streamingText, streamStage]);

  useEffect(() => {
    if (renameTarget && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renameTarget]);

  /** 点击面板外关闭会话下拉。 */
  useEffect(() => {
    if (!sessionMenuOpen) return;
    const onClick = (event: MouseEvent) => {
      if (sessionMenuRef.current && !sessionMenuRef.current.contains(event.target as Node)) {
        setSessionMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [sessionMenuOpen]);

  const activeSession = useMemo<AssistantSession | null>(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  const context = useMemo(
    () => (feature ? buildContextSnapshot(location.pathname, location.search) : null),
    [feature, location.pathname, location.search, bridge],
  );

  const changeDefaultLlm = (configId: string) => {
    setPreferredConfigId(configId);
    if (configId) localStorage.setItem(DEFAULT_LLM_KEY, configId);
    else localStorage.removeItem(DEFAULT_LLM_KEY);
  };

  /** 确保存在可用会话：无会话时按默认 LLM 自动创建。 */
  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (activeSessionId) return activeSessionId;
    if (!preferredConfigId) {
      notify("请先在顶部选择默认 LLM");
      return null;
    }
    try {
      const created = await createAssistantSession("新会话", Number(preferredConfigId));
      await refreshSessions();
      setActiveSessionId(created.id);
      return created.id;
    } catch (caught) {
      notifyError(caught, "创建会话失败");
      return null;
    }
  }, [activeSessionId, preferredConfigId, refreshSessions, setActiveSessionId]);

  /** 局部替换最后一条 assistant 消息为内联错误。 */
  const appendInlineError = useCallback(
    (text: string, code: string, traceId: string | null) => {
      const id = `local-error-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id,
          role: "assistant",
          kind: "normal",
          text,
          page_context: null,
          upstream_metadata: { error: true, code, trace_id: traceId ?? "" },
          trace_id: traceId ?? null,
          error_code: code,
          created_at: new Date().toISOString(),
        },
      ]);
      void refreshSessions();
    },
    [refreshSessions],
  );

  const submit = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      const text = input.trim();
      if (!text || sending) return;
      const sessionId = await ensureSession();
      if (!sessionId) return;
      setSending(true);
      setStreamingText("");
      setStreamStage("thinking");
      const localUserId = `local-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id: localUserId,
          role: "user",
          kind: "normal",
          text,
          page_context: context,
          upstream_metadata: null,
          created_at: new Date().toISOString(),
        },
      ]);
      setInput("");
      let compressed = false;
      try {
        await streamAssistantMessage(
          sessionId,
          { text, page_context: context },
          {
            onCompress: () => {
              compressed = true;
              setStreamStage("compressing");
            },
            onDelta: (delta) => {
              setStreamStage("replying");
              setStreamingText((prev) => (prev ?? "") + delta);
            },
            onDone: (message, _traceId) => {
              setStreamingText(null);
              setStreamStage("idle");
              setMessages((prev) => [...prev, message]);
              if (compressed) {
                notify("历史已压缩");
                compressed = false;
              }
              void refreshSessions();
              // 刷新 usage（压缩/回复后比例会变化）
              getAssistantSession(sessionId)
                .then((detail) => setUsage(detail.usage ?? null))
                .catch(() => undefined);
            },
            onError: (code, messageText, traceId) => {
              setStreamingText(null);
              setStreamStage("idle");
              appendInlineError(`⚠️ ${messageText}`, code, traceId);
            },
          },
        );
        setStreamingText((prev) => (prev === "" ? null : prev));
      } catch (caught) {
        setStreamingText(null);
        setStreamStage("idle");
        if (caught instanceof ApiError && caught.status === 404) {
          try {
            const reply = await sendAssistantMessage(sessionId, {
              text,
              page_context: context,
            });
            setMessages((prev) => [...prev, reply]);
            void refreshSessions();
          } catch (fallbackError) {
            appendInlineError(
              friendlyErrorMessage(fallbackError, "发送失败"),
              "fallback_failed",
              null,
            );
          }
        } else {
          appendInlineError(
            friendlyErrorMessage(caught, "发送失败"),
            "send_failed",
            null,
          );
        }
      } finally {
        setSending(false);
      }
    },
    [input, sending, context, ensureSession, refreshSessions, appendInlineError],
  );

  const copyMessage = async (message: AssistantMessage) => {
    await copyText(message.text, "回复");
  };

  const applyInsert = useCallback(() => {
    const target = currentEditBridge();
    if (!target || insertPreview === null) {
      setInsertPreview(null);
      return;
    }
    target.apply({
      reasoning: null,
      final_text: insertPreview,
      tool_calls: [],
    });
    setInsertPreview(null);
    notify("已覆盖编辑器内容");
  }, [insertPreview]);

  const removeSession = useCallback(async () => {
    if (!activeSessionId) return;
    if (!(await confirmAction({ message: "确认删除当前会话及其消息？" }))) return;
    try {
      await deleteAssistantSession(activeSessionId);
      setActiveSessionId(null);
      setMessages([]);
      setUsage(null);
      await refreshSessions();
      notify("会话已删除", "success");
    } catch (caught) {
      notifyError(caught, "删除失败");
    }
  }, [activeSessionId, refreshSessions, setActiveSessionId]);

  const submitRename = useCallback(async () => {
    if (!renameTarget) return;
    const next = renameTarget.title.trim();
    if (!next) {
      setRenameTarget(null);
      return;
    }
    if (next === activeSession?.title) {
      setRenameTarget(null);
      return;
    }
    try {
      const updated = await patchAssistantSession(renameTarget.id, { title: next });
      setRenameTarget(null);
      await refreshSessions();
      notify("会话已重命名", "success");
      // 直接更新本地，确保下拉中显示
      if (updated && activeSessionId === updated.id) {
        setActiveSessionId(updated.id);
      }
    } catch (caught) {
      notifyError(caught, "重命名失败");
    }
  }, [renameTarget, activeSession?.title, activeSessionId, refreshSessions, setActiveSessionId]);

  const createNewSession = useCallback(async () => {
    if (!preferredConfigId) {
      notify("请先选择默认 LLM");
      return;
    }
    try {
      const created = await createAssistantSession("新会话", Number(preferredConfigId));
      await refreshSessions();
      setActiveSessionId(created.id);
      setSessionMenuOpen(false);
    } catch (caught) {
      notifyError(caught, "创建失败");
    }
  }, [preferredConfigId, refreshSessions, setActiveSessionId]);

  if (!user || user.role === "admin") {
    // 管理员无个人业务场景（无 LLM 配置），不展示助手。
    return null;
  }

  const showWelcome = messages.length === 0 && !streamingText;
  const usageRatio = usage?.ratio ?? 0;
  const usagePercent = Math.min(100, Math.round(usageRatio * 100));
  const usageBarClass =
    usageRatio >= USAGE_WARN_RATIO
      ? "bg-red-500"
      : usageRatio >= 0.6
        ? "bg-amber-400"
        : "bg-primary";

  const statusText =
    streamStage === "compressing"
      ? "压缩历史中…"
      : streamStage === "replying"
        ? "回复中…"
        : streamStage === "thinking"
          ? "思考中…"
          : null;

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="打开小助手"
          className="fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-violet-600 via-primary to-blue-600 text-white shadow-lg transition hover:brightness-110 hover:shadow-xl"
        >
          <Icon name="reply" className="h-5 w-5" />
        </button>
      )}

      {open && (
        <aside
          className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[420px] flex-col border-l border-slate-200 bg-white shadow-xl"
          aria-label="小助手面板"
        >
          <header className="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-violet-600 to-primary text-white">
              <Icon name="reply" className="h-4 w-4" />
            </span>
            <span className="text-sm font-semibold text-slate-800">AI 助手</span>
            <span className="ml-auto flex items-center gap-1">
              <button
                type="button"
                onClick={() => void removeSession()}
                disabled={!activeSessionId}
                className="rounded px-2 py-1 text-xs text-slate-400 hover:text-red-500 disabled:opacity-40"
                aria-label="删除当前会话"
              >
                <Icon name="close" className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:text-slate-700"
                aria-label="收起面板"
              >
                收起
              </button>
            </span>
          </header>

          <div className="relative flex items-center gap-2 border-b border-slate-100 px-4 py-2 text-xs">
            <div ref={sessionMenuRef} className="relative min-w-0 flex-1">
              <button
                type="button"
                onClick={() => setSessionMenuOpen((prev) => !prev)}
                className="field-input flex h-8 w-full items-center justify-between truncate px-2 py-0 text-xs"
                aria-label="选择会话"
                aria-expanded={sessionMenuOpen}
              >
                <span className="truncate">
                  {activeSession ? activeSession.title : "无会话"}
                </span>
                <Icon name="chevronRight" className="h-3 w-3 rotate-90 text-slate-400" />
              </button>
              {sessionMenuOpen && (
                <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg">
                  <button
                    type="button"
                    onClick={() => void createNewSession()}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-primary hover:bg-slate-50"
                  >
                    <Icon name="plus" className="h-3 w-3" />
                    新建会话
                  </button>
                  <div className="my-1 h-px bg-slate-100" />
                  {sessions.length === 0 && (
                    <div className="px-3 py-2 text-xs text-slate-400">暂无历史会话</div>
                  )}
                  {sessions.map((s) => {
                    const isActive = s.id === activeSessionId;
                    const isRenaming = renameTarget?.id === s.id;
                    return (
                      <div
                        key={s.id}
                        className={`flex items-center gap-1 px-2 py-1 hover:bg-slate-50 ${
                          isActive ? "bg-primary/5" : ""
                        }`}
                      >
                        {isRenaming ? (
                          <input
                            ref={renameInputRef}
                            value={renameTarget.title}
                            onChange={(event) =>
                              setRenameTarget({ id: s.id, title: event.target.value })
                            }
                            onBlur={() => void submitRename()}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void submitRename();
                              } else if (event.key === "Escape") {
                                event.preventDefault();
                                setRenameTarget(null);
                              }
                            }}
                            className="field-input h-6 min-h-0 flex-1 px-1 py-0 text-xs"
                          />
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => {
                                setActiveSessionId(s.id);
                                setSessionMenuOpen(false);
                              }}
                              className="flex-1 truncate text-left text-xs text-slate-700"
                            >
                              {s.title}
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                setRenameTarget({ id: s.id, title: s.title })
                              }
                              className="rounded p-1 text-slate-400 hover:text-primary"
                              aria-label="重命名会话"
                            >
                              <Icon name="code" className="h-3 w-3" />
                            </button>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            <select
              value={preferredConfigId}
              onChange={(event) => changeDefaultLlm(event.target.value)}
              className="field-input h-8 w-32 py-0 text-xs"
              aria-label="默认 LLM 配置"
            >
              <option value="">默认 LLM</option>
              {llmConfigs.map((cfg) => (
                <option key={cfg.id} value={cfg.id}>
                  {cfg.name}
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              className="h-8 px-2 text-xs"
              disabled={!preferredConfigId}
              onClick={() => void createNewSession()}
            >
              <Icon name="plus" className="h-3 w-3" />
            </Button>
          </div>

          {usage && activeSessionId && (
            <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-1.5 text-[11px] text-slate-500">
              <div className="flex items-center justify-between">
                <span>上下文使用</span>
                <span className="font-mono text-slate-400">
                  {usage.estimated_tokens} / {usage.limit_tokens} token
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                <div
                  className={`h-full ${usageBarClass} transition-all`}
                  style={{ width: `${usagePercent}%` }}
                />
              </div>
              <div className="mt-0.5 flex items-center justify-between text-[10px] text-slate-400">
                <span>{usage.message_count} 条消息</span>
                {usage.compressing && <span className="text-primary">正在压缩…</span>}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-1.5 text-[11px] text-slate-400">
            <Icon name="link" className="h-3 w-3" />
            {context ? (
              <span>
                上下文：{CONTEXT_FEATURE_LABELS[context.feature] ?? context.feature}
                {context.resource && Object.keys(context.resource).length > 0 && (
                  <span className="ml-1 text-slate-300">
                    （{Object.entries(context.resource)
                      .slice(0, 3)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" · ")}）
                  </span>
                )}
                {context.unsaved_edit && <span className="ml-1 text-primary">含未提交草稿</span>}
              </span>
            ) : (
              <span>当前页面无可用上下文</span>
            )}
          </div>

          <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4 text-sm">
            {showWelcome && (
              <div className="space-y-4">
                <div className="rounded-xl bg-gradient-to-br from-violet-600/10 via-primary/10 to-blue-600/10 px-4 py-5">
                  <h3 className="text-sm font-semibold text-slate-800">页面助手</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    可读取当前页面和任务草稿
                  </p>
                </div>
                <div className="space-y-2">
                  {WELCOME_CARDS.map((card) => (
                    <button
                      key={card.title}
                      type="button"
                      onClick={() => setInput(card.prompt)}
                      className="block w-full rounded-lg border border-slate-200 bg-white px-3.5 py-3 text-left transition hover:border-primary/50 hover:shadow-card"
                    >
                      <span className="block text-xs font-medium text-slate-700">
                        {card.title}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                        {card.prompt}
                      </span>
                    </button>
                  ))}
                </div>
                {!activeSession && (
                  <p className="text-center text-[11px] text-slate-300">
                    {llmConfigs.length === 0
                      ? "暂无可用 LLM 配置，请先创建"
                      : "发送消息后自动创建会话"}
                  </p>
                )}
              </div>
            )}

            {messages.map((message) => {
              if (message.kind === "summary") {
                return (
                  <div key={message.id} className="flex justify-center">
                    <div className="rounded-full bg-amber-50 px-3 py-1 text-[11px] text-amber-600 ring-1 ring-amber-200">
                      📦 历史已压缩（保留早期摘要 + 最近 6 条原文）
                    </div>
                  </div>
                );
              }
              const isError = !!message.error_code || !!message.upstream_metadata?.error;
              if (message.role === "user") {
                return (
                  <div key={message.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-xl rounded-br-sm bg-primary/10 px-3 py-2 text-xs leading-relaxed text-slate-700">
                      <p className="whitespace-pre-wrap break-words">{message.text}</p>
                      {message.page_context && (
                        <div className="mt-1 text-[10px] text-slate-400">
                          页面：{message.page_context.route}
                          {message.page_context.feature &&
                            `（${CONTEXT_FEATURE_LABELS[message.page_context.feature] ?? message.page_context.feature}）`}
                        </div>
                      )}
                    </div>
                  </div>
                );
              }
              return (
                <div key={message.id} className="flex justify-start">
                  <div
                    className={`max-w-[85%] rounded-xl rounded-bl-sm px-3 py-2 text-xs leading-relaxed ${
                      isError
                        ? "bg-red-50 text-red-700 ring-1 ring-red-100"
                        : "bg-slate-100 text-slate-700"
                    }`}
                  >
                    {isError ? (
                      <p className="whitespace-pre-wrap break-words">{message.text}</p>
                    ) : (
                      <MarkdownText text={message.text} />
                    )}
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                      <button
                        type="button"
                        onClick={() => void copyMessage(message)}
                        className="text-slate-400 hover:text-slate-600"
                      >
                        复制
                      </button>
                      {message.trace_id && (
                        <span
                          className="cursor-pointer text-slate-300 hover:text-slate-500"
                          title="点击复制 traceId"
                          onClick={() => void copyText(message.trace_id ?? "", "traceId")}
                        >
                          trace: {message.trace_id.slice(0, 12)}…
                        </span>
                      )}
                      {bridge && !isError && (
                        <button
                          type="button"
                          onClick={() => setInsertPreview(message.text)}
                          className="text-primary hover:underline"
                        >
                          插入到回复编辑器
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

            {(streamingText !== null || statusText) && (
              <div className="flex justify-start">
                <div className="max-w-[85%] rounded-xl rounded-bl-sm bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-700">
                  {streamingText ? (
                    <p className="whitespace-pre-wrap break-words">
                      {streamingText}
                      <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-primary align-middle" />
                    </p>
                  ) : (
                    <p className="text-slate-400">
                      {statusText}
                      <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-slate-400 align-middle" />
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          <form onSubmit={submit} className="flex items-end gap-2 border-t border-slate-100 p-3">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
                if (event.key === "Escape") {
                  setOpen(false);
                }
              }}
              placeholder="输入问题…（Enter 发送）"
              rows={2}
              className="field-input min-h-[44px] flex-1 resize-none text-xs"
            />
            <Button type="submit" loading={sending} disabled={!input.trim()}>
              发送
            </Button>
          </form>
        </aside>
      )}

      {insertPreview !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-slate-800">插入到回复编辑器</h3>
            <p className="mt-1 text-xs text-red-500">将覆盖当前编辑内容，无法撤回。</p>
            <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-slate-100 bg-slate-50 p-3 text-xs text-slate-600">
              {insertPreview}
            </pre>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setInsertPreview(null)}>
                取消
              </Button>
              <Button onClick={applyInsert}>确认覆盖</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

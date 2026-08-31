import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  createAssistantSession,
  deleteAssistantSession,
  getAssistantSession,
  sendAssistantMessage,
  streamAssistantMessage,
} from "../../api/assistant";
import { ApiError } from "../../api/client";
import { listLlmConfigs } from "../../api/llmConfigs";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { AssistantMessage, LlmConfig } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import { currentEditBridge } from "./bridge";
import { buildContextSnapshot, featureForRoute } from "./contextRegistry";
import { useAssistant } from "./AssistantContext";

const CONTEXT_FEATURE_LABELS: Record<string, string> = {
  console: "控制台",
  task_list: "任务记录",
  task_detail: "任务回复",
  api_keys: "API 管理",
  llm_configs: "LLM 管理",
  connections: "连接 IM",
  models: "模型广场",
  invitations: "邀请码",
  users: "用户管理",
  account: "账号设置",
};

/** 默认 LLM 偏好持久化 key（新建会话 / 直接发消息时使用）。 */
const DEFAULT_LLM_KEY = "hlg_assistant_default_llm";

const WELCOME_CARDS: { title: string; prompt: string }[] = [
  {
    title: "解释当前页面",
    prompt: "解释一下当前页面的功能和使用方法",
  },
  {
    title: "起草任务回复",
    prompt: "基于当前任务草稿，帮我起草一份更专业的回复",
  },
  {
    title: "排查连接错误",
    prompt: "总结最近的连接错误并给出排查建议",
  },
];

export function AssistantPanel() {
  const { user } = useAuth();
  const { open, setOpen, sessions, activeSessionId, setActiveSessionId, refreshSessions } =
    useAssistant();
  const location = useLocation();
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [preferredConfigId, setPreferredConfigId] = useState<string>(() =>
    localStorage.getItem(DEFAULT_LLM_KEY) ?? "",
  );
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [insertPreview, setInsertPreview] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const bridge = currentEditBridge();
  const feature = featureForRoute(location.pathname);

  useEffect(() => {
    listLlmConfigs(1)
      .then((page) => setLlmConfigs(page.items.filter((cfg) => cfg.is_enabled)))
      .catch(() => setLlmConfigs([]));
  }, []);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      return;
    }
    getAssistantSession(activeSessionId)
      .then((detail) => setMessages(detail.messages))
      .catch(() => setMessages([]));
  }, [activeSessionId]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, open, streamingText]);

  const activeSession = useMemo(
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
      notify(caught instanceof Error ? caught.message : "创建会话失败");
      return null;
    }
  }, [activeSessionId, preferredConfigId, refreshSessions, setActiveSessionId]);

  const submit = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      const text = input.trim();
      if (!text || sending) return;
      const sessionId = await ensureSession();
      if (!sessionId) return;
      setSending(true);
      setStreamingText("");
      setMessages((prev) => [
        ...prev,
        {
          id: `local-${Date.now()}`,
          role: "user",
          text,
          page_context: context,
          upstream_metadata: null,
          created_at: new Date().toISOString(),
        },
      ]);
      setInput("");
      try {
        await streamAssistantMessage(
          sessionId,
          { text, page_context: context },
          {
            onDelta: (delta) => setStreamingText((prev) => (prev ?? "") + delta),
            onDone: (message) => {
              setStreamingText(null);
              setMessages((prev) => [...prev, message]);
              void refreshSessions();
            },
            onError: (code, message) => {
              setStreamingText(null);
              notify(`回复失败（${code}）：${message}`);
            },
          },
        );
        // 流中 error 已由回调处理；此处兜底 done 未达（异常截断）。
        setStreamingText((prev) => (prev === "" ? null : prev));
      } catch (caught) {
        setStreamingText(null);
        if (caught instanceof ApiError && caught.status === 404) {
          // 流式端点不存在（旧后端）：回退非流式。
          try {
            const reply = await sendAssistantMessage(sessionId, {
              text,
              page_context: context,
            });
            setMessages((prev) => [...prev, reply]);
            void refreshSessions();
          } catch (fallbackError) {
            notify(fallbackError instanceof Error ? fallbackError.message : "发送失败");
          }
        } else {
          notify(caught instanceof Error ? caught.message : "发送失败");
        }
      } finally {
        setSending(false);
      }
    },
    [input, sending, context, ensureSession, refreshSessions],
  );

  const copyMessage = (message: AssistantMessage) => {
    void navigator.clipboard
      .writeText(message.text)
      .then(() => notify("已复制"))
      .catch(() => notify("复制失败"));
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
    if (!window.confirm("确认删除当前会话及其消息？")) return;
    try {
      await deleteAssistantSession(activeSessionId);
      setActiveSessionId(null);
      setMessages([]);
      await refreshSessions();
      notify("会话已删除");
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  }, [activeSessionId, refreshSessions, setActiveSessionId]);

  if (!user || user.role === "admin") {
    // 管理员无个人业务场景（无 LLM 配置），不展示助手。
    return null;
  }

  const showWelcome = messages.length === 0 && !streamingText;

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

          <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2 text-xs">
            <select
              value={activeSessionId ?? ""}
              onChange={(event) => setActiveSessionId(event.target.value || null)}
              className="field-input h-8 min-w-0 flex-1 py-0 text-xs"
              aria-label="选择会话"
            >
              <option value="">无会话</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
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
              onClick={() => {
                void (async () => {
                  try {
                    const created = await createAssistantSession(
                      "新会话",
                      Number(preferredConfigId),
                    );
                    await refreshSessions();
                    setActiveSessionId(created.id);
                    notify("会话已创建");
                  } catch (caught) {
                    notify(caught instanceof Error ? caught.message : "创建失败");
                  }
                })();
              }}
            >
              <Icon name="plus" className="h-3 w-3" />
            </Button>
          </div>

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
              <span>当前页面无上下文</span>
            )}
          </div>

          <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4 text-sm">
            {showWelcome && (
              <div className="space-y-4">
                <div className="rounded-xl bg-gradient-to-br from-violet-600/10 via-primary/10 to-blue-600/10 px-4 py-5">
                  <h3 className="text-sm font-semibold text-slate-800">你好，我是 AI 助手</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    能工智人网关的智能陪伴，可基于当前页面与任务草稿回答问题
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
                      ? "暂无可用的 LLM 配置，请先在「LLM 管理」创建"
                      : "发送第一条消息将按默认 LLM 自动创建会话"}
                  </p>
                )}
              </div>
            )}

            {messages.map((message) => (
              <div
                key={message.id}
                className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={
                    message.role === "user"
                      ? "max-w-[85%] rounded-xl rounded-br-sm bg-primary/10 px-3 py-2 text-xs leading-relaxed text-slate-700"
                      : "max-w-[85%] rounded-xl rounded-bl-sm bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-700"
                  }
                >
                  <p className="whitespace-pre-wrap break-words">{message.text}</p>
                  {message.role === "assistant" && (
                    <div className="mt-1.5 flex items-center gap-2 text-[11px]">
                      <button
                        type="button"
                        onClick={() => copyMessage(message)}
                        className="text-slate-400 hover:text-slate-600"
                      >
                        复制
                      </button>
                      {bridge && (
                        <button
                          type="button"
                          onClick={() => setInsertPreview(message.text)}
                          className="text-primary hover:underline"
                        >
                          插入到回复编辑器
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {streamingText !== null && (
              <div className="flex justify-start">
                <div className="max-w-[85%] rounded-xl rounded-bl-sm bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-700">
                  <p className="whitespace-pre-wrap break-words">
                    {streamingText || "思考中…"}
                    {streamingText && (
                      <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-primary align-middle" />
                    )}
                  </p>
                </div>
              </div>
            )}

            {sending && streamingText === null && (
              <div className="flex justify-start">
                <div className="rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-400">
                  思考中…
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
              placeholder="询问当前页面或草稿…（Enter 发送，Shift+Enter 换行）"
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
            <p className="mt-1 text-xs text-red-500">将覆盖编辑器当前内容（不可撤销）。</p>
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

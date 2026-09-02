import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getConversation, getConversationMessage, listInbox, markTaskSeen } from "../../api/tasks";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { formatDeadline } from "./labels";
import { TaskEditor } from "./TaskEditor";

const POLL_INTERVAL_MS = 3000;

/** 消息体渲染：支持超长折叠的纯文本展示。 */
function MessageText({ text, taskId, messageIndex, length }: { text: string; taskId: string; messageIndex: number; length: number }) {
  const [expanded, setExpanded] = useState(false);
  const [full, setFull] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const overLimit = length > 2000;
  const displayText = expanded ? (full ?? text) : text;
  const toggle = async () => {
    if (expanded || !overLimit) {
      setExpanded(false);
      return;
    }
    setLoading(true);
    try {
      const result = await getConversationMessage(taskId, messageIndex);
      setFull(result.full_text);
      setExpanded(true);
    } catch (caught) {
      console.error(caught);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div>
      <pre className={`whitespace-pre-wrap font-mono text-[11px] text-slate-600 ${expanded ? "" : "line-clamp-6"}`}>
        {displayText || "(空)"}
      </pre>
      {overLimit && (
        <button
          type="button"
          onClick={() => void toggle()}
          className="mt-1 text-xs text-primary hover:underline"
          disabled={loading}
        >
          {expanded ? "收起" : loading ? "加载中…" : `展开全部（${length.toLocaleString()} 字）`}
        </button>
      )}
    </div>
  );
}

export function RepliesWorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const [items, setItems] = useState<import("../../api/tasks").InboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<import("../../api/tasks").ConversationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadInbox = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const result = await listInbox();
        setItems(result.items);
        setSelectedId((current) => {
          if (current && result.items.some((item) => item.id === current)) return current;
          if (focusId && result.items.some((item) => item.id === focusId)) return focusId;
          return null;
        });
        setError("");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "加载失败");
      } finally {
        setLoading(false);
      }
    },
    [focusId],
  );

  useEffect(() => {
    void loadInbox();
  }, [loadInbox]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadInbox(true);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadInbox]);

  const loadConversation = useCallback(async (taskId: string) => {
    try {
      setConversation(await getConversation(taskId));
    } catch {
      setConversation(null);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setConversation(null);
      return;
    }
    void loadConversation(selectedId);
    void markTaskSeen(selectedId).catch(() => undefined);
  }, [selectedId, loadConversation]);

  const handleSubmitted = useCallback(
    (taskId: string) => {
      setSelectedId((prev) => {
        if (prev !== taskId) return prev;
        const next = items.filter((item) => item.id !== taskId);
        return next[0]?.id ?? null;
      });
      void loadInbox(true);
    },
    [items, loadInbox],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="回复工作台"
        actions={
          <Button variant="ghost" onClick={() => void loadInbox()}>
            <Icon name="refresh" className="h-4 w-4" />
            刷新
          </Button>
        }
      />
      {error && <ErrorBanner message={error} />}

      <div className="grid gap-4 md:grid-cols-[minmax(232px,0.8fr)_minmax(0,1.4fr)_minmax(0,1.4fr)]">
        <Card className="h-fit space-y-0 overflow-hidden md:sticky md:top-24">
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
            收件箱
          </div>
          {loading && items.length === 0 && (
            <p className="px-4 py-6 text-center text-xs text-slate-400">加载中…</p>
          )}
          {!loading && items.length === 0 && (
            <p className="px-4 py-6 text-center text-xs text-slate-400">没有待处理任务</p>
          )}
          <ul className="divide-y divide-slate-100">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedId(item.id);
                    setSearchParams({ focus: item.id }, { replace: true });
                  }}
                  className={`flex w-full items-start gap-2 px-4 py-3 text-left transition hover:bg-slate-50 ${
                    selectedId === item.id ? "bg-blue-50/60" : ""
                  }`}
                >
                  {item.unread && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-red-500" />}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate font-mono text-xs font-medium text-slate-700">
                        #{item.public_id}
                      </span>
                      <span className="shrink-0 truncate text-[11px] text-slate-400">
                        {item.fake_model_name}
                      </span>
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                      {item.prompt_preview || "(空提示词)"}
                    </span>
                    {item.human_deadline_at && (
                      <span
                        className="mt-1 inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500"
                        title="剩余时间"
                      >
                        {formatDeadline(item.human_deadline_at)}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="min-h-96 overflow-y-auto">
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700">
            对话
          </div>
          {!selectedId && (
            <p className="grid h-64 place-items-center px-4 text-xs text-slate-400">
              从左侧选择一条任务来查看上下文
            </p>
          )}
          {selectedId && conversation === null && (
            <p className="grid h-64 place-items-center px-4 text-xs text-slate-400">加载中…</p>
          )}
          {conversation && (
            <div className="divide-y divide-slate-100">
              {conversation.messages.map((message) => (
                <div key={message.index} className="px-4 py-3">
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] text-slate-400">
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium capitalize">
                      {message.role}
                    </span>
                    <span>{message.length.toLocaleString()} 字</span>
                    {message.has_more && <span className="text-amber-500">截断</span>}
                  </div>
                  <MessageText
                    text={message.preview}
                    taskId={selectedId || ""}
                    messageIndex={message.index}
                    length={message.length}
                  />
                </div>
              ))}
              {conversation.messages.length === 0 && (
                <p className="px-4 py-6 text-center text-xs text-slate-400">暂无消息</p>
              )}
            </div>
          )}
        </Card>

        <Card className="h-fit md:sticky md:top-24">
          {selectedId ? (
            <TaskEditor
              taskId={selectedId}
              standalone={false}
              onSubmitted={handleSubmitted}
            />
          ) : (
            <p className="grid h-64 place-items-center px-4 py-6 text-center text-xs text-slate-400">
              选择任务后在此回复
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

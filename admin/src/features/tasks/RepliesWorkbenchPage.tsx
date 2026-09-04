import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  getConversation,
  getConversationMessage,
  listInbox,
  markTaskSeen,
  type ConversationBlock,
  type InboxItem,
  type ConversationPage,
} from "../../api/tasks";
import { Card } from "../../components/data-display/Card";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { formatDeadline } from "./labels";
import { TaskEditor } from "./TaskEditor";
import { friendlyErrorMessage } from "../../utils/notify";

const POLL_INTERVAL_MS = 3000;

// ---------------------------------------------------------------------------
// 消息块渲染：文本折叠 / 图片缩略图 / 附件徽章，完整保留多模态内容
// ---------------------------------------------------------------------------

function isImageBlock(block: ConversationBlock): boolean {
  return block.type === "image" && Boolean(block.url);
}

function BlockBadge({ text, tone = "slate" }: { text: string; tone?: "slate" | "amber" }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
        tone === "amber" ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-500"
      }`}
    >
      {text}
    </span>
  );
}

function ImageBlock({ url, width, height }: { url: string; width?: number | null; height?: number | null }) {
  const sizeHint = width && height ? `${width}×${height}` : null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="block overflow-hidden rounded-lg border border-slate-200 transition hover:border-primary/50"
      title={sizeHint ? `点击查看原图（${sizeHint}）` : "点击查看原图"}
    >
      <img
        src={url}
        alt={sizeHint ? `图片 ${sizeHint}` : "图片"}
        loading="lazy"
        className="max-h-48 w-auto max-w-full bg-slate-50 object-contain"
      />
    </a>
  );
}

function FileBlock({ block }: { block: ConversationBlock }) {
  const name = block.name || "未命名附件";
  const mediaType = block.media_type || "未知类型";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <Icon name="link" className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{name}</span>
      <BlockBadge text={mediaType} />
    </div>
  );
}

/** 文本块：超长折叠，展开后按需拉取全文。 */
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

/** 单条消息：按 blocks 完整渲染（文本 / 图片 / 文件），不丢失多模态内容。 */
function MessageBlocks({
  blocks,
  text,
  taskId,
  messageIndex,
  length,
}: {
  blocks: ConversationBlock[];
  text: string;
  taskId: string;
  messageIndex: number;
  length: number;
}) {
  const hasRichBlocks = blocks.some((block) => block.type !== "text");
  if (!hasRichBlocks) {
    return <MessageText text={text} taskId={taskId} messageIndex={messageIndex} length={length} />;
  }
  return (
    <div className="space-y-2">
      {blocks.map((block, index) => {
        if (isImageBlock(block) && block.url) {
          return (
            <ImageBlock key={index} url={block.url} width={block.width} height={block.height} />
          );
        }
        if (block.type === "file" || block.type === "attachment") {
          return <FileBlock key={index} block={block} />;
        }
        if (block.type === "text" && block.text) {
          return (
            <MessageText
              key={index}
              text={block.text}
              taskId={taskId}
              messageIndex={messageIndex}
              length={block.text.length}
            />
          );
        }
        return (
          <div key={index} className="flex items-center gap-2 text-[11px] text-slate-400">
            <BlockBadge text={block.type} tone="amber" />
            <span className="min-w-0 flex-1 truncate font-mono">{block.text || block.name || block.tool_call_id || ""}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面：收件箱 + 完整上下文；回复编辑器在弹窗内打开
// ---------------------------------------------------------------------------

export function RepliesWorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const [items, setItems] = useState<InboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [replyOpen, setReplyOpen] = useState(false);

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
        setError(friendlyErrorMessage(caught, "加载失败"));
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
      setReplyOpen(false);
      setSelectedId((prev) => {
        if (prev !== taskId) return prev;
        const next = items.filter((item) => item.id !== taskId);
        return next[0]?.id ?? null;
      });
      void loadInbox(true);
    },
    [items, loadInbox],
  );

  const selectedItem = items.find((item) => item.id === selectedId) ?? null;

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

      <div className="grid gap-4 md:grid-cols-[minmax(232px,0.9fr)_minmax(0,2.1fr)]">
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
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <span className="text-sm font-medium text-slate-700">对话上下文</span>
            {selectedItem && (
              <Button onClick={() => setReplyOpen(true)}>
                <Icon name="reply" className="h-4 w-4" />
                回复
              </Button>
            )}
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
                  <MessageBlocks
                    blocks={message.blocks}
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
      </div>

      {replyOpen && selectedId && (
        <Modal
          title={`回复任务 #${selectedItem?.public_id ?? ""}`}
          description="人工回复必须先提交完整结果，再进行伪流式输出"
          onClose={() => setReplyOpen(false)}
          width="max-w-6xl"
        >
          <div className="max-h-[84vh] overflow-y-auto p-6">
            <TaskEditor taskId={selectedId} onSubmitted={handleSubmitted} />
          </div>
        </Modal>
      )}
    </div>
  );
}

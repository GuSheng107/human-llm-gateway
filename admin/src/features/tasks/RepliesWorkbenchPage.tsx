import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  getRequestView,
  getRequestViewBlock,
  listInbox,
  markTaskSeen,
  type InboxItem,
  type RequestView,
  type RequestViewBlock,
  type RequestViewContextItem,
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
// 内容块渲染：文本折叠 / 图片 / 附件 / 工具调用徽章，完整保留多模态内容
// ---------------------------------------------------------------------------

function isImageBlock(block: RequestViewBlock): boolean {
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

function ImageBlock({
  url,
  sourceType,
  filename,
}: {
  url: string;
  sourceType?: string | null;
  filename?: string | null;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        图片加载失败{sourceType ? `（来源：${sourceType}）` : ""}
        {filename ? `：${filename}` : ""}，可复制或打开原始地址检查。
        <span className="mt-1 block break-all font-mono text-[10px] text-amber-700">{url}</span>
      </div>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="block overflow-hidden rounded-lg border border-slate-200 transition hover:border-primary/50"
      title="点击查看原图"
    >
      <img
        src={url}
        alt={filename || "图片"}
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-h-48 w-auto max-w-full bg-slate-50 object-contain"
      />
    </a>
  );
}

function FileBlock({ block }: { block: RequestViewBlock }) {
  const name = block.name || block.filename || "未命名附件";
  const mediaType = block.media_type || "未知类型";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <Icon name="link" className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{name}</span>
      <BlockBadge text={mediaType} />
    </div>
  );
}

function ToolCallBlock({ block }: { block: RequestViewBlock }) {
  const [showArgs, setShowArgs] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <div className="flex items-center gap-2">
        <BlockBadge text="tool_call" tone="amber" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-700">
          {block.name || block.text || "(未命名工具)"}
        </span>
        {block.call_id && (
          <span className="shrink-0 font-mono text-[10px] text-slate-400">{block.call_id}</span>
        )}
        <button
          type="button"
          className="shrink-0 text-[11px] text-primary hover:underline"
          onClick={() => setShowArgs((v) => !v)}
        >
          {showArgs ? "收起参数" : "查看参数"}
        </button>
      </div>
      {showArgs && (
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-[10px] text-slate-600">
          {JSON.stringify(block.arguments ?? {}, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** 文本块：超长折叠，展开后按块 ID 拉取全文。 */
function MessageText({
  text,
  taskId,
  blockId,
  length,
  truncated,
}: {
  text: string;
  taskId: string;
  blockId: string;
  length: number;
  truncated?: boolean | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [full, setFull] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const overLimit = truncated === true || length > 2000;
  const displayText = expanded ? (full ?? text) : text;
  const toggle = async () => {
    if (expanded || !overLimit) {
      setExpanded(false);
      return;
    }
    setLoading(true);
    try {
      const result = await getRequestViewBlock(taskId, blockId);
      setFull(typeof result.text === "string" ? result.text : text);
      setExpanded(true);
    } catch (caught) {
      console.error(caught);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div>
      <pre
        className={`whitespace-pre-wrap font-mono text-[11px] text-slate-600 ${
          expanded ? "" : "line-clamp-6"
        }`}
      >
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

/** 单条上下文：按 blocks 完整渲染（文本 / 图片 / 文件 / 工具调用）。 */
function ContextItemBlocks({ item, taskId }: { item: RequestViewContextItem; taskId: string }) {
  return (
    <div className="space-y-2">
      {item.blocks.map((block, index) => {
        if (isImageBlock(block) && block.url) {
          return (
            <ImageBlock
              key={block.id || index}
              url={block.url}
              sourceType={block.source}
              filename={block.filename}
            />
          );
        }
        if (block.type === "file" || block.type === "audio") {
          return <FileBlock key={block.id || index} block={block} />;
        }
        if (block.type === "tool_call") {
          return <ToolCallBlock key={block.id || index} block={block} />;
        }
        if (block.type === "text" && block.text) {
          return (
            <MessageText
              key={block.id || index}
              text={block.text}
              taskId={taskId}
              blockId={block.id}
              length={block.text_length ?? block.text.length}
              truncated={block.truncated}
            />
          );
        }
        return (
          <div
            key={block.id || index}
            className="flex items-center gap-2 text-[11px] text-slate-400"
          >
            <BlockBadge text={block.type} tone="amber" />
            <span className="min-w-0 flex-1 truncate font-mono">
              {block.text || block.name || block.call_id || ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ContextItemViewRow({ item, taskId }: { item: RequestViewContextItem; taskId: string }) {
  return (
    <div className="px-4 py-3">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] text-slate-400">
        <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium capitalize">
          {item.role}
        </span>
        <span>{item.text_length.toLocaleString()} 字</span>
        {item.block_count > 1 && <span>{item.block_count} 块</span>}
      </div>
      <ContextItemBlocks item={item} taskId={taskId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面：收件箱 + 请求视图（RequestView）；回复编辑器在大弹窗内打开
// ---------------------------------------------------------------------------

export function RepliesWorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const [items, setItems] = useState<InboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requestView, setRequestView] = useState<RequestView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [replyOpen, setReplyOpen] = useState(false);
  const [systemExpanded, setSystemExpanded] = useState(false);

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

  const loadRequestView = useCallback(async (taskId: string) => {
    try {
      setRequestView(await getRequestView(taskId));
    } catch {
      setRequestView(null);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setRequestView(null);
      return;
    }
    void loadRequestView(selectedId);
    void markTaskSeen(selectedId).catch(() => undefined);
  }, [selectedId, loadRequestView]);

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

  const renderAttachments = () => {
    if (!requestView || requestView.attachments.length === 0) return null;
    return (
      <div className="border-t border-slate-100">
        <div className="px-4 py-2 text-[11px] font-medium text-slate-500">
          附件（{requestView.attachments.length}）
        </div>
        <div className="space-y-2 px-4 pb-3">
          {requestView.attachments.map((block, index) => {
            if (isImageBlock(block) && block.url) {
              return (
                <ImageBlock
                  key={block.id || index}
                  url={block.url}
                  sourceType={block.source}
                  filename={block.filename}
                />
              );
            }
            return <FileBlock key={block.id || index} block={block} />;
          })}
        </div>
      </div>
    );
  };

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
                    setSystemExpanded(false);
                    setSearchParams({ focus: item.id }, { replace: true });
                  }}
                  className={`flex w-full items-start gap-2 px-4 py-3 text-left transition hover:bg-slate-50 ${
                    selectedId === item.id ? "bg-blue-50/60" : ""
                  }`}
                >
                  {item.unread && (
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-red-500" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate font-mono text-xs font-medium text-slate-700">
                        {item.display_name}
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
            <span className="text-sm font-medium text-slate-700">请求上下文</span>
            <div className="flex items-center gap-2">
              {requestView && requestView.caller_tools.definitions.length > 0 && (
                <span
                  className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700"
                  title="调用方在请求中声明的工具"
                >
                  {requestView.caller_tools.definitions.length} 个调用方工具
                </span>
              )}
              {selectedItem && (
                <Button onClick={() => setReplyOpen(true)}>
                  <Icon name="reply" className="h-4 w-4" />
                  回复
                </Button>
              )}
            </div>
          </div>
          {!selectedId && (
            <p className="grid h-64 place-items-center px-4 text-xs text-slate-400">
              从左侧选择一条任务来查看上下文
            </p>
          )}
          {selectedId && requestView === null && (
            <p className="grid h-64 place-items-center px-4 text-xs text-slate-400">加载中…</p>
          )}
          {requestView && (
            <div className="divide-y divide-slate-100">
              {requestView.caller_system.items.length > 0 && (
                <div>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between px-4 py-2 text-left text-[11px] font-medium text-slate-500 transition hover:bg-slate-50"
                    onClick={() => setSystemExpanded((v) => !v)}
                  >
                    <span>
                      技术上下文（调用方 system · {requestView.caller_system.item_count} 条 ·{" "}
                      {requestView.caller_system.character_count.toLocaleString()} 字）
                    </span>
                    <span className={`text-slate-400 transition ${systemExpanded ? "rotate-90" : ""}`}>
                      ›
                    </span>
                  </button>
                  {systemExpanded && (
                    <div className="border-t border-slate-100 bg-slate-50/60">
                      {requestView.caller_system.items.map((item) => (
                        <ContextItemViewRow
                          key={item.id}
                          item={item}
                          taskId={requestView.task.id}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="bg-blue-50/30">
                <div className="px-4 py-1.5 text-[11px] font-medium text-blue-700">本次输入</div>
                {requestView.current_input.map((item) => (
                  <ContextItemViewRow key={item.id} item={item} taskId={requestView.task.id} />
                ))}
              </div>
              {requestView.attached_context.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-[11px] font-medium text-slate-500">
                    附带上下文（{requestView.attached_context.length} 条）
                  </div>
                  {requestView.attached_context.map((item) => (
                    <ContextItemViewRow key={item.id} item={item} taskId={requestView.task.id} />
                  ))}
                </div>
              )}
              {renderAttachments()}
              {requestView.current_input.length === 0 &&
                requestView.caller_system.items.length === 0 && (
                  <p className="px-4 py-6 text-center text-xs text-slate-400">暂无内容</p>
                )}
            </div>
          )}
        </Card>
      </div>

      {replyOpen && selectedId && (
        <Modal
          title={selectedItem?.display_name ?? "回复任务"}
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

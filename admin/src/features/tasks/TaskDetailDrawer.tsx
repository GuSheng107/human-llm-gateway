import { useCallback, useEffect, useState } from "react";
import { generateDraft, getTask } from "../../api/tasks";
import { listLlmConfigs } from "../../api/llmConfigs";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Drawer } from "../../components/feedback/Drawer";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { LlmConfig, TaskDetail, TaskEvent } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import {
  ACTOR_TYPE_LABELS,
  DELIVERY_MODE_LABELS,
  DRAFT_SOURCE_LABELS,
  DRAFT_STATE_LABELS,
  EVENT_TYPE_LABELS,
  PROTOCOL_LABELS,
  REPLY_STRATEGY_LABELS,
  formatDateTime,
  formatDeadline,
} from "./labels";
import { ReplyEditor } from "./ReplyEditor";

function MetaCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="block text-slate-400">{label}</span>
      <span className="mt-1 block font-medium text-slate-700">{children}</span>
    </div>
  );
}

function EventRow({ event }: { event: TaskEvent }) {
  const payloadText =
    event.payload ? JSON.stringify(event.payload) : null;
  return (
    <li className="flex gap-3 py-2.5">
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-slate-700">
            {EVENT_TYPE_LABELS[event.event_type] ?? event.event_type}
          </span>
          <span className="text-slate-400">{formatDateTime(event.created_at)}</span>
        </div>
        <div className="mt-0.5 text-slate-400">
          {ACTOR_TYPE_LABELS[event.actor_type] ?? event.actor_type}
          {event.actor_user_id ? ` · 用户 ${event.actor_user_id}` : ""}
        </div>
        {payloadText && (
          <code className="mt-1 block break-all rounded bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-500">
            {payloadText}
          </code>
        )}
      </div>
    </li>
  );
}

export function TaskDetailDrawer({
  taskId,
  onClose,
  onChanged,
}: {
  taskId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { user: currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [showGenerateModal, setShowGenerateModal] = useState(false);
  const [generatedDraftId, setGeneratedDraftId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      setDetail(await getTask(taskId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    }
  }, [taskId]);

  const loadLlmConfigs = useCallback(async () => {
    try {
      const result = await listLlmConfigs(1);
      setLlmConfigs(result.items.filter((cfg) => cfg.is_enabled));
    } catch {
      // LLM 配置拉取失败不阻塞任务详情
      setLlmConfigs([]);
    }
  }, []);

  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (isAdmin) return;
    void loadLlmConfigs();
  }, [isAdmin, loadLlmConfigs]);

  const handleGenerate = async (llmConfigId: number) => {
    setGenerating(true);
    setGenerateError("");
    try {
      const draft = await generateDraft(taskId, llmConfigId);
      notify("已生成草稿，请继续编辑");
      setGeneratedDraftId(draft.id);
      setShowGenerateModal(false);
      await load();
      onChanged();
    } catch (caught) {
      setGenerateError(caught instanceof Error ? caught.message : "生成失败");
    } finally {
      setGenerating(false);
    }
  };

  if (error && !detail) {
    return (
      <Drawer title="任务详情" onClose={onClose} width="max-w-2xl">
        <div className="p-6">
          <ErrorBanner message={error} />
        </div>
      </Drawer>
    );
  }

  if (!detail) {
    return (
      <Drawer title="任务详情" onClose={onClose} width="max-w-2xl">
        <div className="p-6 text-center text-slate-400">加载中…</div>
      </Drawer>
    );
  }

  // 草稿列表仅呈现可编辑项；SUBMITTED 已由「已接受回复」区域展示，去重。
  const editingDrafts = detail.drafts.filter((draft) => draft.state === "editing");

  return (
    <>
      <Drawer
        title={`任务 #${detail.public_id}`}
        description={detail.fake_model_name}
        onClose={onClose}
        width="max-w-2xl"
      >
        <div className="space-y-5 p-6 text-xs">
          <section className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-100 bg-slate-50 p-4">
            <StatusBadge status={detail.state} />
            <span className="text-slate-400">
              {PROTOCOL_LABELS[detail.protocol] ?? detail.protocol}
            </span>
            <span className="text-slate-400">
              {REPLY_STRATEGY_LABELS[detail.reply_strategy] ?? detail.reply_strategy}
            </span>
            <span className="text-slate-400">
              {DELIVERY_MODE_LABELS[detail.delivery_mode] ?? detail.delivery_mode}
              {detail.stream_requested && " · 流式"}
            </span>
            {detail.has_tools && (
              <span className="inline-flex items-center gap-1 text-primary">
                <Icon name="code" className="h-3.5 w-3.5" />
                含工具
              </span>
            )}
            {detail.can_edit && (
              <div className="ml-auto flex items-center gap-2">
                {llmConfigs.length > 0 && (
                  <Button
                    variant="ghost"
                    onClick={() => setShowGenerateModal(true)}
                  >
                    <Icon name="gateway" className="h-4 w-4" />
                    生成草稿
                  </Button>
                )}
                <Button onClick={() => setEditing(true)}>
                  <Icon name="reply" className="h-4 w-4" />
                  撰写回复
                </Button>
              </div>
            )}
          </section>

          <section className="grid grid-cols-2 gap-4 rounded-lg border border-slate-100 p-4 sm:grid-cols-3">
            <MetaCell label="请求模型">{detail.requested_model}</MetaCell>
            <MetaCell label="Fake Model">{detail.fake_model_name}</MetaCell>
            <MetaCell label="API Key">{detail.api_key_prefix}</MetaCell>
            <MetaCell label="创建时间">{formatDateTime(detail.created_at)}</MetaCell>
            <MetaCell label="人工截止">
              {detail.human_deadline_at
                ? `${formatDateTime(detail.human_deadline_at)}（${formatDeadline(detail.human_deadline_at)}）`
                : "-"}
            </MetaCell>
            <MetaCell label="完成时间">{formatDateTime(detail.completed_at)}</MetaCell>
            {detail.response_id && <MetaCell label="响应 ID">{detail.response_id}</MetaCell>}
            {detail.previous_task_id && (
              <MetaCell label="前置任务">#{detail.previous_task_id}</MetaCell>
            )}
            {isAdmin && (
              <MetaCell label="归属用户">{detail.owner_username ?? "-"}</MetaCell>
            )}
          </section>

          {(detail.public_error_code || detail.cancel_reason_code) && (
            <section className="rounded-lg border border-red-100 bg-red-50 p-4">
              <h3 className="text-sm font-medium text-red-700">异常信息</h3>
              <div className="mt-2 space-y-1 text-red-600">
                {detail.public_error_code && (
                  <div>错误码：{detail.public_error_code}</div>
                )}
                {detail.cancel_reason_code && (
                  <div>取消原因：{detail.cancel_reason_code}</div>
                )}
              </div>
            </section>
          )}

          <section>
            <h3 className="mb-2 text-sm font-medium text-slate-700">提示词</h3>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-100 bg-slate-50 p-4 font-mono text-[11px] text-slate-600">
              {detail.prompt_text || "(空)"}
            </pre>
            {!detail.is_owner && (
              <p className="mt-1 text-slate-400">非归属用户仅显示前 200 字</p>
            )}
          </section>

          {detail.tool_names.length > 0 && (
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">声明的工具</h3>
              <div className="flex flex-wrap gap-2">
                {detail.tool_names.map((name) => (
                  <span
                    key={name}
                    className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 font-mono text-[11px] text-slate-600"
                  >
                    {name}
                  </span>
                ))}
              </div>
            </section>
          )}

          {detail.is_owner && detail.raw_request && (
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">原始请求</h3>
              <details>
                <summary className="cursor-pointer text-slate-500">展开完整 JSON</summary>
                <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-slate-100 bg-slate-50 p-4 font-mono text-[11px] text-slate-600">
                  {JSON.stringify(detail.raw_request, null, 2)}
                </pre>
              </details>
            </section>
          )}

          {detail.is_owner && editingDrafts.length > 0 && (
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">
                草稿（{editingDrafts.length}）
              </h3>
              <div className="space-y-2">
                {editingDrafts.map((draft) => (
                  <Card key={draft.id}>
                    <div className="flex items-center justify-between p-3">
                      <div className="flex items-center gap-2">
                        <StatusBadge status="active" fallback={DRAFT_STATE_LABELS[draft.state] ?? draft.state} />
                        <span className="text-slate-400">
                          {DRAFT_SOURCE_LABELS[draft.source] ?? draft.source}
                        </span>
                        {draft.id === detail.active_draft_id && (
                          <span className="text-primary">活动</span>
                        )}
                      </div>
                      <span className="text-slate-400">{formatDateTime(draft.updated_at)}</span>
                    </div>
                  </Card>
                ))}
              </div>
              {/* 已提交草稿由下方「已接受回复」区域呈现，不在此重复列出 */}
            </section>
          )}

          {detail.is_owner && detail.result_draft && (
            <section>
              <h3 className="mb-2 text-sm font-medium text-slate-700">已接受回复</h3>
              <div className="space-y-2 rounded-lg border border-emerald-100 bg-emerald-50/50 p-4">
                {detail.result_draft.reasoning && (
                  <div>
                    <span className="text-slate-400">思考</span>
                    <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-600">
                      {detail.result_draft.reasoning}
                    </pre>
                  </div>
                )}
                {detail.result_draft.tool_calls.length > 0 && (
                  <div>
                    <span className="text-slate-400">工具调用</span>
                    <ul className="mt-1 space-y-1">
                      {detail.result_draft.tool_calls.map((call) => (
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
                    {detail.result_draft.final_text}
                  </pre>
                </div>
              </div>
            </section>
          )}

          <section>
            <h3 className="mb-2 text-sm font-medium text-slate-700">事件时间线</h3>
            {detail.events.length > 0 ? (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-100 px-4">
                {detail.events.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </ul>
            ) : (
              <p className="text-slate-400">暂无事件</p>
            )}
          </section>
        </div>
      </Drawer>

      {showGenerateModal && detail && (
        <Modal
          title="调用 LLM 生成草稿"
          description="选择一个同协议的 LLM 配置；生成结果进入可编辑草稿，不会自动提交。"
          onClose={() => setShowGenerateModal(false)}
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
                        {cfg.protocol === "anthropic" ? "Anthropic" : "OpenAI 兼容"}
                        {" · "}
                        {cfg.real_model}
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
              跨协议生成按字段矩阵自动转换；不可等价的专有字段将被拒绝。
            </p>
          </div>
        </Modal>
      )}

      {generatedDraftId && detail && (
        <Modal
          title="草稿已生成"
          description="上游结果已保存为可编辑草稿，进入编辑器调整后提交。"
          onClose={() => setGeneratedDraftId(null)}
        >
          <div className="flex justify-end gap-2 p-6">
            <Button variant="ghost" onClick={() => setGeneratedDraftId(null)}>
              稍后编辑
            </Button>
            <Button
              onClick={() => {
                setGeneratedDraftId(null);
                setEditing(true);
              }}
            >
              <Icon name="reply" className="h-4 w-4" />
              立即编辑
            </Button>
          </div>
        </Modal>
      )}

      {editing && detail && (
        <ReplyEditor
          task={detail}
          onClose={() => setEditing(false)}
          onSubmitted={() => {
            setEditing(false);
            void load();
            onChanged();
          }}
        />
      )}
    </>
  );
}

import { type FormEvent, useMemo, useState } from "react";
import { saveDraft, submitReply, updateDraft } from "../../api/tasks";
import { Card } from "../../components/data-display/Card";
import { Drawer } from "../../components/feedback/Drawer";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { FormField } from "../../components/form/FormField";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { ReplyDraft, TaskDetail, ToolCall } from "../../types/gateway";
import { isEmptyDraft, parseReply, serializeReply } from "./dsl";

interface ToolCallEditor {
  id: string;
  name: string;
  argumentsText: string;
}

function toEditors(draft: ReplyDraft | null): ToolCallEditor[] {
  if (!draft || draft.tool_calls.length === 0) {
    return [{ id: "", name: "", argumentsText: "{}" }];
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
    if (!editor.id.trim() || !editor.name.trim()) {
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

export function ReplyEditor({
  task,
  onClose,
  onSubmitted,
}: {
  task: TaskDetail;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const activeDraft = useMemo(() => {
    if (task.active_draft_id) {
      const found = task.drafts.find((d) => d.id === task.active_draft_id);
      if (found && found.state === "editing") return found;
    }
    return null;
  }, [task]);

  const initial = activeDraft ?? task.result_draft;
  const [reasoning, setReasoning] = useState(initial?.reasoning ?? "");
  const [finalText, setFinalText] = useState(initial?.final_text ?? "");
  const [toolCalls, setToolCalls] = useState<ToolCallEditor[]>(() => toEditors(initial));
  const [activeDraftId, setActiveDraftId] = useState<string | null>(
    activeDraft?.id ?? null,
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [preview, setPreview] = useState<ReplyDraft | null>(null);
  const [dslInput, setDslInput] = useState("");
  const [dslError, setDslError] = useState("");

  const liveDraft = useMemo(() => {
    const result = buildDraft(reasoning, toolCalls, finalText);
    return result.ok ? result.draft : null;
  }, [reasoning, toolCalls, finalText]);

  const liveDsl = useMemo(
    () => (liveDraft ? serializeReply(liveDraft) : ""),
    [liveDraft],
  );

  const updateCall = (index: number, patch: Partial<ToolCallEditor>) => {
    setToolCalls((prev) => prev.map((call, i) => (i === index ? { ...call, ...patch } : call)));
  };

  const addCall = () => {
    setToolCalls((prev) => [
      ...prev,
      { id: nextCallId(prev), name: "", argumentsText: "{}" },
    ]);
  };

  const removeCall = (index: number) => {
    setToolCalls((prev) =>
      prev.length === 1 ? [{ id: "", name: "", argumentsText: "{}" }] : prev.filter((_, i) => i !== index),
    );
  };

  const saveDraftHandler = async (event: FormEvent) => {
    event.preventDefault();
    const result = buildDraft(reasoning, toolCalls, finalText);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (activeDraftId) {
        const updated = await updateDraft(task.id, activeDraftId, result.draft);
        setActiveDraftId(updated.id);
      } else {
        const created = await saveDraft(task.id, result.draft);
        setActiveDraftId(created.id);
      }
      notify("草稿已保存");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const openPreview = () => {
    const result = buildDraft(reasoning, toolCalls, finalText);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (isEmptyDraft(result.draft)) {
      setError("回复内容不能为空");
      return;
    }
    setError("");
    setPreview(result.draft);
  };

  const confirmSubmit = async () => {
    if (!preview) return;
    setSubmitting(true);
    setError("");
    try {
      await submitReply(task.id, preview, activeDraftId ?? undefined);
      notify("回复已提交");
      setPreview(null);
      onSubmitted();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交失败");
      setPreview(null);
    } finally {
      setSubmitting(false);
    }
  };

  const importDsl = () => {
    setDslError("");
    if (!dslInput.trim()) {
      setDslError("请粘贴 DSL 文本");
      return;
    }
    try {
      const parsed = parseReply(dslInput);
      setReasoning(parsed.reasoning ?? "");
      setFinalText(parsed.final_text ?? "");
      setToolCalls(
        parsed.tool_calls.length > 0
          ? parsed.tool_calls.map((call) => ({
              id: call.id,
              name: call.name,
              argumentsText: JSON.stringify(call.arguments, null, 2),
            }))
          : [{ id: "", name: "", argumentsText: "{}" }],
      );
      notify("已从 DSL 导入");
    } catch (caught) {
      setDslError(caught instanceof Error ? caught.message : "DSL 解析失败");
    }
  };

  return (
    <Drawer
      title={`撰写回复 · #${task.public_id}`}
      description="思考、最终文本与假 tool call；提交后不可撤销"
      onClose={onClose}
      width="max-w-2xl"
    >
      <form onSubmit={saveDraftHandler} className="space-y-5 p-6 text-xs">
        {error && <ErrorBanner message={error} />}

        <FormField label="思考（可选）" hint="人工推理过程，不会作为最终回复输出">
          <textarea
            value={reasoning}
            onChange={(event) => setReasoning(event.target.value)}
            className="field-input min-h-[80px] font-mono"
            placeholder="::: reasoning 围栏块的等价内容"
          />
        </FormField>

        <FormField label="假 Tool Call" hint="仅作为回复结构转发，系统不会执行">
          <div className="space-y-3">
            {toolCalls.map((call, index) => (
              <Card key={index}>
                <div className="space-y-3 p-3">
                  <div className="flex gap-2">
                    <input
                      value={call.id}
                      onChange={(event) => updateCall(index, { id: event.target.value })}
                      className="field-input w-32 font-mono"
                      placeholder="call_01"
                    />
                    <input
                      value={call.name}
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
                    onChange={(event) => updateCall(index, { argumentsText: event.target.value })}
                    className="field-input min-h-[72px] font-mono text-[11px]"
                    placeholder='{"key":"value"}'
                  />
                </div>
              </Card>
            ))}
            <Button type="button" variant="ghost" onClick={addCall}>
              <Icon name="plus" className="h-3.5 w-3.5" />
              添加工具调用
            </Button>
          </div>
        </FormField>

        <FormField label="最终回复文本" required hint="将作为 Fake Model 的最终输出返回">
          <textarea
            value={finalText}
            onChange={(event) => setFinalText(event.target.value)}
            className="field-input min-h-[120px]"
            placeholder="面向调用方的最终回复内容"
          />
        </FormField>

        <details className="rounded-lg border border-slate-100 bg-slate-50/60">
          <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-slate-600">
            DSL 双向同步（与 IM 共享同一结构）
          </summary>
          <div className="space-y-3 px-4 pb-4">
            <div>
              <span className="block text-slate-400">当前编辑器序列化为 DSL</span>
              <pre className="mt-1 max-h-40 overflow-auto rounded border border-slate-200 bg-white p-3 font-mono text-[11px] text-slate-600">
                {liveDsl || "(空)"}
              </pre>
            </div>
            <div>
              <span className="block text-slate-400">从 DSL 导入（粘贴后解析）</span>
              <textarea
                value={dslInput}
                onChange={(event) => setDslInput(event.target.value)}
                className="field-input mt-1 min-h-[72px] font-mono text-[11px]"
                placeholder="::: reasoning&#10;...&#10;:::"
              />
              {dslError && <p className="mt-1 text-red-500">{dslError}</p>}
              <Button type="button" variant="ghost" className="mt-2" onClick={importDsl}>
                <Icon name="upload" className="h-3.5 w-3.5" />
                解析并导入
              </Button>
            </div>
          </div>
        </details>

        <div className="flex items-center justify-between border-t border-slate-100 pt-4">
          <span className="text-slate-400">
            {activeDraftId ? "已有活动草稿，保存将覆盖" : "尚无草稿，保存将新建"}
          </span>
          <div className="flex gap-2">
            <Button type="button" variant="danger" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" variant="ghost" loading={saving}>
              保存草稿
            </Button>
            <Button type="button" onClick={openPreview}>
              预览并提交
            </Button>
          </div>
        </div>
      </form>

      {preview && (
        <Modal
          title="确认提交回复"
          description="首个有效提交成功后不可撤销或覆盖"
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
              <Button onClick={confirmSubmit} loading={submitting}>
                <Icon name="check" className="h-4 w-4" />
                确认提交
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </Drawer>
  );
}

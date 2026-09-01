import { useEffect, useState, type FormEvent } from "react";
import {
  CAPABILITY_LABELS,
  ENDPOINT_LABELS,
  createFakeModel,
  updateFakeModel,
  type FakeModelPayload,
  type FakeModelUpdatePayload,
} from "../../api/models";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { FakeModel } from "../../types/gateway";

const CAPABILITY_OPTIONS = Object.keys(CAPABILITY_LABELS);
const ENDPOINT_OPTIONS = Object.keys(ENDPOINT_LABELS);
const CONTEXT_PRESETS = [128_000, 256_000, 512_000, 1_000_000];

interface ModelEditModalProps {
  model: FakeModel | null;
  onClose: () => void;
  onSaved: () => void;
}

interface FormState {
  model_id: string;
  display_name: string;
  description: string;
  context_window: string;
  max_output_tokens: string;
  capabilities: string[];
  endpoint_type: string;
}

const EMPTY_FORM: FormState = {
  model_id: "",
  display_name: "",
  description: "",
  context_window: "",
  max_output_tokens: "",
  capabilities: [],
  endpoint_type: "openai_chat",
};

function fromModel(model: FakeModel): FormState {
  return {
    model_id: model.model_id,
    display_name: model.display_name ?? "",
    description: model.description ?? "",
    context_window: model.context_window ? String(model.context_window) : "",
    max_output_tokens: model.max_output_tokens ? String(model.max_output_tokens) : "",
    capabilities: [...model.capabilities],
    endpoint_type: model.endpoint_type,
  };
}

function intOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

type TabKey = "basic" | "capabilities";

const TABS: { key: TabKey; label: string }[] = [
  { key: "basic", label: "基本" },
  { key: "capabilities", label: "能力" },
];

export function ModelEditModal({ model, onClose, onSaved }: ModelEditModalProps) {
  const [tab, setTab] = useState<TabKey>("basic");
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTab("basic");
    setForm(model ? fromModel(model) : EMPTY_FORM);
  }, [model]);

  const patch = (changes: Partial<FormState>) =>
    setForm((previous) => ({ ...previous, ...changes }));

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!form.model_id.trim()) {
      notify("model_id 不能为空");
      return;
    }
    setSaving(true);
    try {
      if (model) {
        const payload: FakeModelUpdatePayload = {
          display_name: form.display_name.trim() || null,
          description: form.description.trim() || null,
          context_window: intOrNull(form.context_window),
          max_output_tokens: intOrNull(form.max_output_tokens),
          capabilities: form.capabilities,
          endpoint_type: form.endpoint_type,
        };
        await updateFakeModel(model.id, payload);
        notify("模型已更新");
      } else {
        const payload: FakeModelPayload = {
          model_id: form.model_id.trim(),
          display_name: form.display_name.trim() || null,
          description: form.description.trim() || null,
          context_window: intOrNull(form.context_window),
          max_output_tokens: intOrNull(form.max_output_tokens),
          capabilities: form.capabilities,
          endpoint_type: form.endpoint_type,
        };
        await createFakeModel(payload);
        notify("模型已创建");
      }
      onSaved();
      onClose();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={model ? `编辑模型 · ${model.model_id}` : "新建模型"}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div className="flex gap-1 border-b border-slate-100 px-6 pt-4">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
              className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium transition ${
                tab === item.key
                  ? "border-primary text-primary"
                  : "border-transparent text-slate-400 hover:text-slate-600"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="max-h-[60vh] space-y-4 overflow-y-auto p-6">
          {tab === "basic" && (
            <>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  model_id<span className="ml-0.5 text-danger">*</span>
                </span>
                <input
                  value={form.model_id}
                  disabled={model !== null}
                  onChange={(event) => patch({ model_id: event.target.value })}
                  className="field-input font-mono disabled:bg-slate-50 disabled:text-slate-400"
                  placeholder="例如：human-gateway-plus"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
                <input
                  value={form.display_name}
                  onChange={(event) => patch({ display_name: event.target.value })}
                  className="field-input"
                  placeholder="可留空"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">描述</span>
                <textarea
                  value={form.description}
                  onChange={(event) => patch({ description: event.target.value })}
                  className="field-input min-h-20"
                  placeholder="可留空"
                />
              </label>
            </>
          )}

          {tab === "capabilities" && (
            <>
              <div>
                <span className="mb-1.5 block text-xs font-medium text-slate-600">能力标签</span>
                <div className="flex flex-wrap gap-2">
                  {CAPABILITY_OPTIONS.map((capability) => {
                    const active = form.capabilities.includes(capability);
                    return (
                      <button
                        key={capability}
                        type="button"
                        onClick={() =>
                          patch({
                            capabilities: active
                              ? form.capabilities.filter((item) => item !== capability)
                              : [...form.capabilities, capability],
                          })
                        }
                        className={`rounded-full border px-3 py-1 text-xs transition ${
                          active
                            ? "border-primary bg-primary/5 text-primary"
                            : "border-slate-200 text-slate-500 hover:border-slate-300"
                        }`}
                      >
                        {CAPABILITY_LABELS[capability]}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div>
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  端点协议<span className="ml-0.5 text-danger">*</span>
                </span>
                <div className="space-y-2">
                  {ENDPOINT_OPTIONS.map((endpoint) => (
                    <label
                      key={endpoint}
                      className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-600"
                    >
                      <input
                        type="radio"
                        name="endpoint_type"
                        checked={form.endpoint_type === endpoint}
                        onChange={() => patch({ endpoint_type: endpoint })}
                      />
                      {ENDPOINT_LABELS[endpoint]}
                    </label>
                  ))}
                </div>
              </div>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  上下文窗口（tokens）
                </span>
                <input
                  value={form.context_window}
                  onChange={(event) => patch({ context_window: event.target.value })}
                  className="field-input"
                  inputMode="numeric"
                  placeholder="留空表示未设置"
                />
                <div className="mt-1.5 flex gap-1.5">
                  {CONTEXT_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => patch({ context_window: String(preset) })}
                      className="rounded border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500 transition hover:border-primary hover:text-primary"
                    >
                      {preset >= 1_000_000 ? `${preset / 1_000_000}M` : `${preset / 1000}k`}
                    </button>
                  ))}
                </div>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  最大输出（tokens）
                </span>
                <input
                  value={form.max_output_tokens}
                  onChange={(event) => patch({ max_output_tokens: event.target.value })}
                  className="field-input"
                  inputMode="numeric"
                  placeholder="留空表示未设置"
                />
              </label>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 p-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" loading={saving}>
            <Icon name="check" className="h-4 w-4" />
            {model ? "保存" : "创建"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

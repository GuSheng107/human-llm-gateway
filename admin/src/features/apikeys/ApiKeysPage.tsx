import { useCallback, useEffect, useState } from "react";
import { createApiKey, deleteApiKey, listApiKeys, updateApiKey } from "../../api/apiKeys";
import { listAllConnections, listPlatforms } from "../../api/connections";
import { listLlmConfigs } from "../../api/llmConfigs";
import { listFakeModels, listModelGroups } from "../../api/models";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { useAuth } from "../auth/AuthContext";
import type {
  ApiKey,
  ApiKeyCreated,
  ImConnection,
  LlmConfig,
  ModelGroup,
} from "../../types/gateway";

const PAGE_SIZE = 20;

const STRATEGY_LABEL: Record<string, string> = {
  human: "人工回复",
  llm: "直接转发 LLM",
  human_fallback_llm: "人工优先，超时降级 LLM",
};

const DELIVERY_LABEL: Record<string, string> = {
  web: "Web 工作台",
  im: "IM 连接",
};

export function ApiKeysPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<ApiKey[]>([]);
  const [connections, setConnections] = useState<ImConnection[]>([]);
  const [deliveryPlatforms, setDeliveryPlatforms] = useState<Set<string>>(new Set());
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [models, setModels] = useState<{ id: string; model_id: string }[]>([]);
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [form, setForm] = useState<{
    id?: string;
    name: string;
    enabled: boolean;
    delivery_mode: "web" | "im";
    im_connection_id: string;
    reply_strategy: "human" | "llm" | "human_fallback_llm";
    llm_config_id: string;
    human_timeout_seconds: number;
    model_group_id: string;
    fake_model_ids: number[];
  } | null>(null);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const isAdmin = user?.role === "admin";

  // 第一层候选集：选了分组就收窄为该组成员；未选分组则为全部可见模型。
  const selectedGroup = groups.find((group) => group.id === form?.model_group_id);
  const candidateModels = selectedGroup
    ? models.filter((model) => selectedGroup.model_ids.includes(model.model_id))
    : models;

  // 切换分组时丢弃已选但不在新候选集内的模型（只能收窄，不能扩张）。
  const onGroupChange = (groupId: string) => {
    if (!form) return;
    const group = groups.find((item) => item.id === groupId);
    const allowed = group ? new Set(group.model_ids) : null;
    setForm({
      ...form,
      model_group_id: groupId,
      fake_model_ids: allowed
        ? form.fake_model_ids.filter((id) => {
            const model = models.find((item) => Number(item.id) === id);
            return model ? allowed.has(model.model_id) : false;
          })
        : form.fake_model_ids,
    });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, allConnections, groupPage, modelPage, platformList, llmPage] = await Promise.all([
        listApiKeys(page),
        listAllConnections(),
        listModelGroups(),
        listFakeModels(),
        listPlatforms(),
        listLlmConfigs(1),
      ]);
      setItems(list.items);
      setTotal(list.total);
      setConnections(allConnections);
      setDeliveryPlatforms(
        new Set(platformList.filter((spec) => spec.supports_delivery).map((spec) => spec.code)),
      );
      setGroups(groupPage.items);
      setModels(modelPage.items.map((item) => ({ id: item.id, model_id: item.model_id })));
      setLlmConfigs(llmPage.items.filter((cfg) => cfg.is_enabled));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => void load(), [load]);

  const openCreate = () => {
    setFormError("");
    setForm({
      name: "",
      enabled: true,
      delivery_mode: "web",
      im_connection_id: "",
      reply_strategy: "human",
      llm_config_id: "",
      human_timeout_seconds: 300,
      model_group_id: "",
      fake_model_ids: [],
    });
  };

  const openEdit = (key: ApiKey) => {
    setFormError("");
    setForm({
      id: key.id,
      name: key.name,
      enabled: key.is_enabled,
      delivery_mode: key.delivery_mode,
      im_connection_id: key.im_connection_id ?? "",
      reply_strategy: key.reply_strategy,
      llm_config_id: key.llm_config_id ?? "",
      human_timeout_seconds: key.human_timeout_seconds,
      model_group_id: key.model_group_id ?? "",
      fake_model_ids: key.fake_model_ids.map(Number),
    });
  };

  const submit = async () => {
    if (!form) return;
    const usesLlm = form.reply_strategy !== "human";
    const payload = {
      name: form.name.trim(),
      enabled: form.enabled,
      delivery_mode: form.delivery_mode,
      im_connection_id: form.delivery_mode === "im" ? Number(form.im_connection_id) : null,
      reply_strategy: form.reply_strategy,
      llm_config_id: usesLlm && form.llm_config_id ? Number(form.llm_config_id) : null,
      human_timeout_seconds: form.human_timeout_seconds,
      model_group_id: form.model_group_id ? Number(form.model_group_id) : null,
      fake_model_ids: form.fake_model_ids,
    };
    setSaving(true);
    setFormError("");
    try {
      if (form.id) {
        await updateApiKey(form.id, payload);
        notify("API Key 已更新");
      } else {
        setCreated(await createApiKey(payload));
      }
      setForm(null);
      await load();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (key: ApiKey) => {
    try {
      await updateApiKey(key.id, { enabled: !key.is_enabled });
      notify(key.is_enabled ? "Key 已停用" : "Key 已启用");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败");
    }
  };

  const remove = async (key: ApiKey) => {
    if (!(await confirmAction({ message: `确认删除 Key「${key.name}」？删除后立即阻止新请求。` }))) return;
    try {
      await deleteApiKey(key.id);
      notify("Key 已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  // 复制完整 Key：策略为直接转发 / 超时降级 LLM 时先做泄露风险提示。
  const copyFullKey = async (key: ApiKey) => {
    if (!key.key) return;
    if (key.reply_strategy !== "human") {
      const confirmed = await confirmAction({
        title: "复制 API Key",
        message:
          "该 Key 会把请求转发到真实 LLM 上游，请勿在公开渠道分享，避免被他人盗用消耗额度。",
        confirmLabel: "我已知晓，继续复制",
        variant: "primary",
      });
      if (!confirmed) return;
    }
    await copyText(key.key, "API Key");
  };

  // 创建弹窗里的复制：同样按策略提示泄露风险。
  const copyCreatedKey = async () => {
    if (!created) return;
    if (created.reply_strategy !== "human") {
      const confirmed = await confirmAction({
        title: "复制 API Key",
        message:
          "该 Key 会把请求转发到真实 LLM 上游，请勿在公开渠道分享，避免被他人盗用消耗额度。",
        confirmLabel: "我已知晓，继续复制",
        variant: "primary",
      });
      if (!confirmed) return;
    }
    const result = await copyText(created.plaintext, "API Key");
    if (result.ok) setCreated(null);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="API 管理"
        actions={
          !isAdmin ? (
            <Button onClick={openCreate}>
              <Icon name="plus" className="h-4 w-4" />
              新建 Key
            </Button>
          ) : undefined
        }
      />

      <Card>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[980px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">API Key</th>
                <th className="px-4 py-3 font-medium">入口</th>
                <th className="px-4 py-3 font-medium">回复策略</th>
                <th className="px-4 py-3 font-medium">模型筛选</th>
                <th className="px-4 py-3 font-medium">状态</th>
                {isAdmin && <th className="px-4 py-3 font-medium">所有者</th>}
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((key) => (
                <tr key={key.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{key.name}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {key.key ? (
                      <span className="inline-flex items-start gap-1.5 font-mono">
                        <span className="break-all text-[11px] leading-5">{key.key}</span>
                        <button
                          type="button"
                          aria-label="复制完整 API Key"
                          title="复制完整 Key"
                          onClick={() => void copyFullKey(key)}
                          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-primary"
                        >
                          <Icon name="copy" className="h-3.5 w-3.5" />
                        </button>
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 font-mono">
                        {key.key_prefix}…
                        <button
                          type="button"
                          aria-label="复制 API Key 前缀"
                          title="复制前缀"
                          onClick={() => void copyText(key.key_prefix, "API Key 前缀")}
                          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-primary"
                        >
                          <Icon name="copy" className="h-3.5 w-3.5" />
                        </button>
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {DELIVERY_LABEL[key.delivery_mode] ?? key.delivery_mode}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {STRATEGY_LABEL[key.reply_strategy] ?? key.reply_strategy}
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 text-slate-500">
                    {key.fake_model_names.length
                      ? key.fake_model_names.join("、")
                      : key.model_group_id
                        ? "按分组"
                        : "全部可见模型"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={key.is_enabled ? "active" : "inactive"} />
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-slate-500">{key.owner_username ?? "-"}</td>
                  )}
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    {!isAdmin && (
                      <button onClick={() => openEdit(key)} className="text-primary">
                        编辑
                      </button>
                    )}
                    <button onClick={() => void toggle(key)} className="text-primary">
                      {key.is_enabled ? "停用" : "启用"}
                    </button>
                    <button onClick={() => void remove(key)} className="text-red-500">
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={isAdmin ? 9 : 8} className="px-4 py-12 text-center text-slate-400">
                    暂无 API Key
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>

      {form && (
        <Modal
          title={form.id ? "编辑 API Key" : "新建 API Key"}
          description="Secret 只在创建时展示一次，请立即复制保存。"
          onClose={() => setForm(null)}
        >
          <div className="space-y-4 p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                名称<span className="ml-0.5 text-danger">*</span>
              </span>
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                className="field-input"
                maxLength={100}
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">回复入口</span>
                <select
                  value={form.delivery_mode}
                  onChange={(event) =>
                    setForm({ ...form, delivery_mode: event.target.value as "web" | "im" })
                  }
                  className="field-input"
                >
                  <option value="web">Web 工作台</option>
                  <option value="im">IM 连接</option>
                </select>
              </label>
              {form.delivery_mode === "im" && (
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-slate-600">
                    IM 连接<span className="ml-0.5 text-danger">*</span>
                  </span>
                  <select
                    value={form.im_connection_id}
                    onChange={(event) => setForm({ ...form, im_connection_id: event.target.value })}
                    className="field-input"
                  >
                    <option value="">请选择连接</option>
                    {connections
                      .filter(
                        (connection) =>
                          (connection.bound && deliveryPlatforms.has(connection.platform)) ||
                          connection.id === form.im_connection_id,
                      )
                      .map((connection) => (
                        <option key={connection.id} value={connection.id}>
                          {connection.name}
                        </option>
                      ))}
                  </select>
                </label>
              )}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">回复策略</span>
                <select
                  value={form.reply_strategy}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      reply_strategy: event.target.value as "human" | "llm" | "human_fallback_llm",
                    })
                  }
                  className="field-input"
                >
                  <option value="human">人工回复</option>
                  <option value="human_fallback_llm">人工优先，超时降级 LLM</option>
                  <option value="llm">直接转发 LLM</option>
                </select>
              </label>
              {form.reply_strategy !== "human" && (
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-slate-600">
                    LLM 配置（支持跨协议）<span className="ml-0.5 text-danger">*</span>
                  </span>
                  <select
                    value={form.llm_config_id}
                    onChange={(event) =>
                      setForm({ ...form, llm_config_id: event.target.value })
                    }
                    className="field-input"
                  >
                    <option value="">请选择 LLM 配置</option>
                    {llmConfigs.map((cfg) => (
                      <option key={cfg.id} value={cfg.id}>
                        {cfg.name}（
                        {cfg.protocol === "anthropic_messages"
                          ? "Anthropic"
                          : cfg.protocol === "openai_responses"
                            ? "OpenAI Responses"
                            : "OpenAI Chat"}
                        ）
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  人工超时（秒，10-1800）<span className="ml-0.5 text-danger">*</span>
                </span>
                <input
                  type="number"
                  min={10}
                  max={1800}
                  value={form.human_timeout_seconds}
                  onChange={(event) =>
                    setForm({ ...form, human_timeout_seconds: Number(event.target.value) })
                  }
                  className="field-input"
                />
              </label>
            </div>

            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                模型分组（可选，第一层筛选）
              </span>
              <select
                value={form.model_group_id}
                onChange={(event) => onGroupChange(event.target.value)}
                className="field-input"
              >
                <option value="">不限制分组</option>
                {groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </label>

            <div className="rounded-md border border-slate-200 bg-slate-50/60 p-4">
              <p className="mb-2 text-xs font-medium text-slate-600">
                直接选择模型（可选）
                {selectedGroup && (
                  <span className="ml-2 font-normal text-slate-400">
                    已按「{selectedGroup.name}」筛选，共 {candidateModels.length} 个
                  </span>
                )}
              </p>
              <div className="max-h-40 space-y-2 overflow-y-auto">
                {candidateModels.map((model) => (
                  <label key={model.id} className="flex items-center gap-3 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      checked={form.fake_model_ids.includes(Number(model.id))}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          fake_model_ids: event.target.checked
                            ? [...form.fake_model_ids, Number(model.id)]
                            : form.fake_model_ids.filter((value) => value !== Number(model.id)),
                        })
                      }
                    />
                    <span className="font-mono text-slate-700">{model.model_id}</span>
                  </label>
                ))}
              </div>
            </div>

            {form.id && (
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                />
                启用（停用后立即阻止新请求）
              </label>
            )}

            {formError && (
              <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
                {formError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setForm(null)}>
                取消
              </Button>
              <Button
                onClick={() => void submit()}
                loading={saving}
                disabled={
                  !form.name.trim() ||
                  (form.delivery_mode === "im" && !form.im_connection_id) ||
                  (form.reply_strategy !== "human" && !form.llm_config_id)
                }
              >
                <Icon name="check" className="h-4 w-4" />
                保存
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {created && (
        <Modal
          title="API Key 已创建"
          onClose={() => setCreated(null)}
        >
          <div className="space-y-4 p-6">
            <div className="break-all rounded-md border border-blue-100 bg-blue-50 p-4 font-mono text-sm text-blue-700">
              {created.plaintext}
            </div>
            <div className="flex justify-end">
              <Button
                onClick={() => void copyCreatedKey()}
              >
                <Icon name="copy" className="h-4 w-4" />
                复制
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

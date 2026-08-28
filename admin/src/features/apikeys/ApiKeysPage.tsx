import { useCallback, useEffect, useState } from "react";
import { createApiKey, deleteApiKey, listApiKeys, updateApiKey } from "../../api/apiKeys";
import { listConnections } from "../../api/connections";
import { listFakeModels, listModelGroups } from "../../api/models";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { useAuth } from "../auth/AuthContext";
import type { ApiKey, ApiKeyCreated, ImConnection, ModelGroup } from "../../types/gateway";

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
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [models, setModels] = useState<{ id: string; model_id: string }[]>([]);
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
    human_timeout_seconds: number;
    model_group_id: string;
    fake_model_ids: number[];
  } | null>(null);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, connectionPage, groupPage, modelPage] = await Promise.all([
        listApiKeys(page),
        listConnections(1),
        listModelGroups(),
        listFakeModels(),
      ]);
      setItems(list.items);
      setTotal(list.total);
      setConnections(connectionPage.items);
      setGroups(groupPage.items);
      setModels(modelPage.items.map((item) => ({ id: item.id, model_id: item.model_id })));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => void load(), [load]);

  const openCreate = () =>
    setForm({
      name: "",
      enabled: true,
      delivery_mode: "web",
      im_connection_id: "",
      reply_strategy: "human",
      human_timeout_seconds: 300,
      model_group_id: "",
      fake_model_ids: [],
    });

  const openEdit = (key: ApiKey) =>
    setForm({
      id: key.id,
      name: key.name,
      enabled: key.is_enabled,
      delivery_mode: key.delivery_mode,
      im_connection_id: key.im_connection_id ?? "",
      reply_strategy: key.reply_strategy,
      human_timeout_seconds: key.human_timeout_seconds,
      model_group_id: key.model_group_id ?? "",
      fake_model_ids: key.fake_model_ids.map(Number),
    });

  const submit = async () => {
    if (!form) return;
    const payload = {
      name: form.name.trim(),
      enabled: form.enabled,
      delivery_mode: form.delivery_mode,
      im_connection_id: form.delivery_mode === "im" ? Number(form.im_connection_id) : null,
      reply_strategy: form.reply_strategy,
      human_timeout_seconds: form.human_timeout_seconds,
      model_group_id: form.model_group_id ? Number(form.model_group_id) : null,
      fake_model_ids: form.fake_model_ids,
    };
    if (form.id) {
      await updateApiKey(form.id, payload);
      notify("API Key 已更新");
      setForm(null);
      await load();
      return;
    }
    setCreated(await createApiKey(payload));
    setForm(null);
    await load();
  };

  const toggle = async (key: ApiKey) => {
    await updateApiKey(key.id, { enabled: !key.is_enabled });
    notify(key.is_enabled ? "Key 已停用" : "Key 已启用");
    await load();
  };

  const remove = async (key: ApiKey) => {
    if (!window.confirm(`确认删除 Key「${key.name}」？删除后立即阻止新请求。`)) return;
    await deleteApiKey(key.id);
    notify("Key 已删除");
    await load();
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="API 管理"
        description="Key 决定请求归属、回复入口、回复策略和对外可用 Fake Model。"
        actions={
          <Button onClick={openCreate}>
            <Icon name="plus" className="h-4 w-4" />
            新建 Key
          </Button>
        }
      />

      <Card>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[980px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">前缀</th>
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
                  <td className="px-4 py-3 font-mono text-slate-500">{key.key_prefix}…</td>
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
                        ? "按分组候选集"
                        : "全部可见模型"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={key.is_enabled ? "active" : "inactive"} />
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-slate-500">{key.owner_username ?? "-"}</td>
                  )}
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => openEdit(key)} className="text-primary">
                      编辑
                    </button>
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
          description="Secret 只在创建时展示一次；模型选择只能收窄候选集。"
          onClose={() => setForm(null)}
        >
          <div className="space-y-4 p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">名称</span>
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
                  <span className="mb-1.5 block text-xs font-medium text-slate-600">IM 连接</span>
                  <select
                    value={form.im_connection_id}
                    onChange={(event) => setForm({ ...form, im_connection_id: event.target.value })}
                    className="field-input"
                  >
                    <option value="">请选择连接</option>
                    {connections.map((connection) => (
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
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  人工超时（秒，10-1800）
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
                onChange={(event) => setForm({ ...form, model_group_id: event.target.value })}
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
                直接选择模型（可选，只收窄不扩张）
              </p>
              <div className="max-h-40 space-y-2 overflow-y-auto">
                {models.map((model) => (
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

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setForm(null)}>
                取消
              </Button>
              <Button
                onClick={() => void submit()}
                disabled={
                  !form.name.trim() ||
                  (form.delivery_mode === "im" && !form.im_connection_id)
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
          description="关闭后不再显示明文，请立即复制并妥善保存。"
          onClose={() => setCreated(null)}
        >
          <div className="space-y-4 p-6">
            <div className="break-all rounded-md border border-blue-100 bg-blue-50 p-4 font-mono text-sm text-blue-700">
              {created.plaintext}
            </div>
            <div className="flex justify-end">
              <Button
                onClick={() => {
                  void navigator.clipboard.writeText(created.plaintext);
                  notify("已复制 API Key");
                }}
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

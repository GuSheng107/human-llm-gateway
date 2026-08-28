import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  createProvider,
  deleteProvider,
  listProviders,
  syncProviderModels,
  updateProvider,
  validateProvider,
} from "../../api/llm";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Icon } from "../../icons";
import type { Provider } from "../../types/llm";

const PROTOCOL_LABELS: Record<string, string> = {
  openai_compatible: "OpenAI 兼容",
  anthropic: "Anthropic",
};

export function ProvidersPage() {
  const location = useLocation();
  const isAdminView = location.pathname.startsWith("/admin");

  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [form, setForm] = useState({ name: "", base_url: "", protocol: "openai_compatible", api_key: "" });
  const [editForm, setEditForm] = useState({ name: "", base_url: "", api_key: "" });
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listProviders();
      setProviders(result.items);
    } catch (error) {
      notify(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await createProvider(form);
      notify("供应商已创建，建议先验证配置再同步模型");
      setCreateOpen(false);
      setForm({ name: "", base_url: "", protocol: "openai_compatible", api_key: "" });
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (provider: Provider) => {
    setEditing(provider);
    setEditForm({ name: provider.name, base_url: provider.base_url, api_key: "" });
  };

  const submitEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = { name: editForm.name, base_url: editForm.base_url };
      if (editForm.api_key) payload.api_key = editForm.api_key;
      await updateProvider(editing.id, payload);
      notify("供应商已更新");
      setEditing(null);
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "更新失败");
    } finally {
      setSubmitting(false);
    }
  };

  const doValidate = async (provider: Provider) => {
    try {
      const result = await validateProvider(provider.id);
      if (result.valid) notify(`验证通过，上游可用模型 ${result.model_count} 个`);
      else notify(`验证失败：${result.error ?? "未知错误"}`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "验证失败");
    }
  };

  const doSync = async (provider: Provider) => {
    setSyncingId(provider.id);
    try {
      const result = await syncProviderModels(provider.id);
      notify(`已同步 ${result.data.length} 个模型，可在创建路由时选择`);
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "同步失败");
    } finally {
      setSyncingId(null);
    }
  };

  const doDelete = async (provider: Provider) => {
    if (!window.confirm(`确定删除供应商「${provider.name}」？`)) return;
    try {
      await deleteProvider(provider.id);
      notify("供应商已删除");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除失败");
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">LLM 供应商</h1>
          <p className="mt-2 text-xs leading-5 text-slate-400">
            配置你自己的真实 LLM 供应商：创建 → 验证 → 同步模型，然后在「模型路由」中选择上游模型。
          </p>
        </div>
        {!isAdminView && (
          <button type="button" onClick={() => setCreateOpen(true)} className="inline-flex items-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#337ecc]">
            <Icon name="plus" className="h-4 w-4" />创建供应商
          </button>
        )}
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <th className="px-5 py-3">名称</th>
              <th className="px-4 py-3">协议</th>
              {isAdminView && <th className="px-4 py-3">所属用户</th>}
              <th className="px-4 py-3">Base URL</th>
              <th className="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr><td colSpan={5} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
            ) : providers.length === 0 ? (
              <tr><td colSpan={5} className="px-5 py-16 text-center text-xs text-slate-400">还没有供应商</td></tr>
            ) : providers.map((provider) => (
              <tr key={provider.id} className="text-xs">
                <td className="px-5 py-4 font-medium text-slate-800">{provider.name}</td>
                <td className="px-4 py-4">
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                    {PROTOCOL_LABELS[provider.protocol] ?? provider.protocol}
                  </span>
                </td>
                {isAdminView && <td className="px-4 py-4 text-slate-600">{provider.owner_name}</td>}
                <td className="max-w-60 truncate px-4 py-4 font-mono text-[11px] text-slate-500" title={provider.base_url}>{provider.base_url}</td>
                <td className="px-5 py-4">
                  {!isAdminView && (
                    <div className="flex flex-wrap items-center justify-end gap-1.5">
                      <button type="button" onClick={() => void doValidate(provider)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:border-[#a0cfff] hover:text-[#409eff]">验证</button>
                      <button type="button" disabled={syncingId === provider.id} onClick={() => void doSync(provider)} className="rounded border border-[#b3d8ff] bg-[#ecf5ff] px-2.5 py-1.5 text-[11px] text-[#409eff] hover:bg-[#d9ecff] disabled:opacity-50">
                        {syncingId === provider.id ? "同步中…" : "同步模型"}
                      </button>
                      <button type="button" onClick={() => openEdit(provider)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">编辑</button>
                      <button type="button" onClick={() => void doDelete(provider)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {createOpen && (
        <Modal title="创建 LLM 供应商" description="API Key 将加密存储，后续不可查看明文。" onClose={() => setCreateOpen(false)}>
          <form onSubmit={submitCreate} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">名称</span>
              <input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：我的 DeepSeek" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">协议</span>
              <select value={form.protocol} onChange={(event) => setForm({ ...form, protocol: event.target.value })} className="field-input">
                <option value="openai_compatible">OpenAI 兼容</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">Base URL</span>
              <input required type="url" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://api.deepseek.com/v1" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">API Key</span>
              <input type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder="sk-…" className="field-input" />
            </label>
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setCreateOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "创建中…" : "创建"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {editing && (
        <Modal title={`编辑供应商 · ${editing.name}`} description="API Key 留空表示保留原值。" onClose={() => setEditing(null)}>
          <form onSubmit={submitEdit} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">名称</span>
              <input required value={editForm.name} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">Base URL</span>
              <input required type="url" value={editForm.base_url} onChange={(event) => setEditForm({ ...editForm, base_url: event.target.value })} className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">API Key <span className="ml-1 font-normal text-slate-400">留空保留原值</span></span>
              <input type="password" value={editForm.api_key} onChange={(event) => setEditForm({ ...editForm, api_key: event.target.value })} className="field-input" />
            </label>
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setEditing(null)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "保存中…" : "保存"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

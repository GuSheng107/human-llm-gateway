import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { createApiKey, deleteApiKey, listApiKeys, toggleApiKey } from "../../api/llm";
import { listConnections } from "../../api/connections";
import { listRoutes } from "../../api/llm";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Icon } from "../../icons";
import type { ApiKey, ApiKeyCreated, ModelRoute } from "../../types/llm";
import type { IMConnection } from "../../types/connections";

const MODE_LABELS: Record<string, string> = {
  human: "人工",
  llm: "LLM",
  human_fallback_llm: "人工→LLM",
};

export function ApiKeysPage() {
  const location = useLocation();
  const isAdminView = location.pathname.startsWith("/admin");

  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [routes, setRoutes] = useState<ModelRoute[]>([]);
  const [connections, setConnections] = useState<IMConnection[]>([]);
  const [form, setForm] = useState({ name: "", route_id: "", binding_type: "web" as "web" | "im", im_connection_id: "" });
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listApiKeys();
      setKeys(result.items);
    } catch (error) {
      notify(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCreateOptions = useCallback(async () => {
    try {
      const [routesResult, connectionsResult] = await Promise.all([
        listRoutes(),
        listConnections(),
      ]);
      setRoutes(routesResult.items);
      setConnections(connectionsResult.filter((c: IMConnection) => c.binding_status === "bound"));
    } catch (error) {
      notify(error instanceof Error ? error.message : "选项加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (createOpen) void loadCreateOptions();
  }, [createOpen, loadCreateOptions]);

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      const result = await createApiKey({
        name: form.name.trim(),
        route_id: Number(form.route_id),
        im_connection_id: form.binding_type === "im" && form.im_connection_id
          ? Number(form.im_connection_id)
          : null,
      });
      setCreateOpen(false);
      setCreated(result);
      setForm({ name: "", route_id: "", binding_type: "web", im_connection_id: "" });
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const doDelete = async (key: ApiKey) => {
    if (!window.confirm(`确定删除 API Key「${key.name}」？使用该 Key 的客户端将立即失效。`)) return;
    try {
      await deleteApiKey(key.id);
      notify("API Key 已删除");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除失败");
    }
  };

  const doToggle = async (key: ApiKey) => {
    try {
      const result = await toggleApiKey(key.id);
      notify(result.active ? "已启用" : "已停用");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "操作失败");
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">API Key</h1>
          <p className="mt-2 max-w-2xl text-xs leading-5 text-slate-400">
            一条 Key 绑定一个回复通道：绑 IM 连接的 Key 把任务投递到你的 IM；纯 Web Key 在「任务」页等待网页回复。
            明文只在创建时展示一次。
          </p>
        </div>
        {!isAdminView && (
          <button type="button" onClick={() => setCreateOpen(true)} className="inline-flex items-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#337ecc]">
            <Icon name="plus" className="h-4 w-4" />签发 API Key
          </button>
        )}
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <th className="px-5 py-3">名称</th>
              <th className="px-4 py-3">前缀</th>
              <th className="px-4 py-3">通道</th>
              <th className="px-4 py-3">路由 / 模式</th>
              {isAdminView && <th className="px-4 py-3">所属用户</th>}
              <th className="px-4 py-3">状态</th>
              <th className="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr><td colSpan={7} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
            ) : keys.length === 0 ? (
              <tr><td colSpan={7} className="px-5 py-16 text-center text-xs text-slate-400">还没有 API Key</td></tr>
            ) : keys.map((key) => (
              <tr key={key.id} className="text-xs">
                <td className="px-5 py-4 font-medium text-slate-800">{key.name}</td>
                <td className="px-4 py-4 font-mono text-[11px] text-slate-500">{key.prefix}…</td>
                <td className="px-4 py-4">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${key.binding_type === "im" ? "bg-emerald-50 text-emerald-600" : "bg-violet-50 text-violet-600"}`}>
                    {key.binding_type === "im" ? `IM · ${key.im_name}` : "纯 Web"}
                  </span>
                </td>
                <td className="px-4 py-4 text-slate-600">
                  {key.model_name}
                  <span className="ml-2 text-[10px] text-slate-400">{MODE_LABELS[key.route_mode]}</span>
                </td>
                {isAdminView && <td className="px-4 py-4 text-slate-600">{key.operator_name}</td>}
                <td className="px-4 py-4">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${key.active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400"}`}>
                    {key.active ? "启用" : "停用"}
                  </span>
                </td>
                <td className="px-5 py-4">
                  {!isAdminView && (
                    <div className="flex items-center justify-end gap-1.5">
                      <button type="button" onClick={() => void doToggle(key)} className="rounded border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 hover:text-slate-800">
                        {key.active ? "停用" : "启用"}
                      </button>
                      <button type="button" onClick={() => void doDelete(key)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {createOpen && (
        <Modal title="签发 API Key" description="明文只在创建完成后展示一次，请立即保存。" onClose={() => setCreateOpen(false)} width="max-w-2xl">
          <form onSubmit={submitCreate} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">Key 名称</span>
              <input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：我的编码助手" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">绑定路由</span>
              <select required value={form.route_id} onChange={(event) => setForm({ ...form, route_id: event.target.value })} className="field-input">
                <option value="">请选择…</option>
                {routes.map((route) => (
                  <option key={route.id} value={route.id}>
                    {route.name} · {route.model_name} · {MODE_LABELS[route.mode]}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-[11px] text-slate-400">没有可选路由？先到「模型路由」创建。</p>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">回复通道</span>
              <select value={form.binding_type} onChange={(event) => setForm({ ...form, binding_type: event.target.value as "web" | "im" })} className="field-input">
                <option value="web">纯 Web（在任务页回复）</option>
                <option value="im">投递到 IM 连接</option>
              </select>
            </label>
            {form.binding_type === "im" && (
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">IM 连接（仅已绑定身份的连接）</span>
                <select required value={form.im_connection_id} onChange={(event) => setForm({ ...form, im_connection_id: event.target.value })} className="field-input">
                  <option value="">请选择…</option>
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name} · {connection.platform}
                    </option>
                  ))}
                </select>
                {connections.length === 0 && (
                  <p className="mt-1.5 text-[11px] text-amber-500">没有已绑定的连接：先到「我的连接」完成绑定。</p>
                )}
              </label>
            )}
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setCreateOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "创建中…" : "签发"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {created && (
        <Modal title="API Key 已创建" description="明文只展示这一次，关闭后无法再次查看。" onClose={() => setCreated(null)}>
          <div className="px-6 py-6">
            <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-4">
              <div className="text-[11px] text-blue-500">完整 API Key</div>
              <div className="mt-2 flex items-center justify-between gap-3">
                <code className="break-all font-mono text-sm font-semibold text-blue-700">{created.secret}</code>
                <button type="button" onClick={() => { void navigator.clipboard.writeText(created.secret); notify("已复制到剪贴板"); }} className="rounded-md border border-blue-200 bg-white p-2 text-blue-500 hover:bg-blue-50">
                  <Icon name="copy" className="h-4 w-4" />
                </button>
              </div>
            </div>
            <p className="mt-4 text-[11px] leading-5 text-slate-400">
              在客户端使用 <code className="rounded bg-slate-100 px-1">Authorization: Bearer {created.prefix}…</code> 调用 /v1/chat/completions 等接口。
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  createCatalogEntry,
  createRoute,
  deleteCatalogEntry,
  deleteRoute,
  listCatalog,
  listProviderModels,
  listProviders,
  listRoutes,
} from "../../api/llm";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Pagination } from "../../components/data-display/Pagination";
import { Icon } from "../../icons";
import { useAuth } from "../auth/AuthContext";
import type { CatalogEntry, ModelRoute, Provider, RouteMode, SyncedModel } from "../../types/llm";

const MODE_LABELS: Record<RouteMode, { label: string; className: string; hint: string }> = {
  human: { label: "人工", className: "bg-emerald-50 text-emerald-700", hint: "全部投递到 IM / 等待网页回复" },
  llm: { label: "LLM", className: "bg-blue-50 text-blue-700", hint: "直连上游模型" },
  human_fallback_llm: { label: "人工→LLM", className: "bg-purple-50 text-purple-700", hint: "人工超时后回退 LLM" },
};

export function RoutesPage() {
  const { user } = useAuth();
  const location = useLocation();
  const isAdminView = location.pathname.startsWith("/admin");

  const [tab, setTab] = useState<"routes" | "catalog">("routes");
  const [routes, setRoutes] = useState<ModelRoute[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const [providers, setProviders] = useState<Provider[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    name: "", model_name: "", upstream_model: "", mode: "human" as RouteMode,
    provider_id: "" as string, human_timeout_seconds: 300,
  });
  const [providerModels, setProviderModels] = useState<SyncedModel[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const [catalogFormOpen, setCatalogFormOpen] = useState(false);
  const [catalogForm, setCatalogForm] = useState({ model_id: "", owned_by: "" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [routesResult, catalogResult, providersResult] = await Promise.all([
        listRoutes(page),
        listCatalog(),
        listProviders(),
      ]);
      setRoutes(routesResult.items);
      setTotal(routesResult.total);
      setCatalog(catalogResult);
      setProviders(providersResult.items);
    } catch (error) {
      notify(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!form.provider_id) {
      setProviderModels([]);
      return;
    }
    listProviderModels(Number(form.provider_id))
      .then((result) => setProviderModels(result.data))
      .catch(() => setProviderModels([]));
  }, [form.provider_id]);

  const selectedProvider = providers.find((p) => String(p.id) === form.provider_id);
  const needsUpstream = form.mode === "llm" || form.mode === "human_fallback_llm";

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await createRoute({
        name: form.name.trim(),
        model_name: form.model_name,
        upstream_model: form.upstream_model,
        mode: form.mode,
        provider_id: needsUpstream && form.provider_id ? Number(form.provider_id) : null,
        human_timeout_seconds: form.human_timeout_seconds,
      });
      notify("路由已创建");
      setCreateOpen(false);
      setForm({ name: "", model_name: "", upstream_model: "", mode: "human", provider_id: "", human_timeout_seconds: 300 });
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const submitCatalog = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await createCatalogEntry(catalogForm);
      notify("目录项已添加");
      setCatalogFormOpen(false);
      setCatalogForm({ model_id: "", owned_by: "" });
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "添加失败");
    } finally {
      setSubmitting(false);
    }
  };

  const doDeleteRoute = async (route: ModelRoute) => {
    if (!window.confirm(`确定删除路由「${route.name}」？`)) return;
    try {
      await deleteRoute(route.id);
      notify("路由已删除");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除失败");
    }
  };

  const doDeleteCatalog = async (entry: CatalogEntry) => {
    if (!window.confirm(`确定从目录移除「${entry.model_id}」？`)) return;
    try {
      await deleteCatalogEntry(entry.id);
      notify("目录项已删除");
      await load();
    } catch (error) {
      notify(error instanceof Error ? error.message : "删除失败");
    }
  };

  return (
    <div className="mx-auto max-w-[1300px] space-y-5">
      <section className="flex flex-col justify-between gap-4 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-center">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">模型路由</h1>
          <p className="mt-2 max-w-2xl text-xs leading-5 text-slate-400">
            客户端提交的 model <b>不决定</b>实际上游模型；路由里的「实际上游模型」才是权威值。
            人工模式下无论客户端请求哪个 fake 模型，都会投递到绑定的 IM 或等待网页回复。
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => setTab("routes")} className={`rounded-md px-3 py-2 text-xs ${tab === "routes" ? "bg-[#409eff] text-white" : "border border-slate-200 text-slate-500"}`}>路由列表</button>
          {user?.role === "admin" && (
            <button type="button" onClick={() => setTab("catalog")} className={`rounded-md px-3 py-2 text-xs ${tab === "catalog" ? "bg-[#409eff] text-white" : "border border-slate-200 text-slate-500"}`}>对外模型目录</button>
          )}
        </div>
      </section>

      {tab === "routes" && (
        <>
          {!isAdminView && (
            <div className="flex justify-end">
              <button type="button" onClick={() => setCreateOpen(true)} className="inline-flex items-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#337ecc]">
                <Icon name="plus" className="h-4 w-4" />创建路由
              </button>
            </div>
          )}
          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
                  <th className="px-5 py-3">名称</th>
                  <th className="px-4 py-3">对外模型名</th>
                  <th className="px-4 py-3">实际上游模型</th>
                  <th className="px-4 py-3">模式</th>
                  <th className="px-4 py-3">供应商</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {loading ? (
                  <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
                ) : routes.length === 0 ? (
                  <tr><td colSpan={6} className="px-5 py-16 text-center text-xs text-slate-400">还没有路由</td></tr>
                ) : routes.map((route) => (
                  <tr key={route.id} className="text-xs">
                    <td className="px-5 py-4 font-medium text-slate-800">{route.name}</td>
                    <td className="px-4 py-4">
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700">{route.model_name}</code>
                    </td>
                    <td className="px-4 py-4">
                      {route.mode === "human" ? (
                        <span className="text-[11px] text-slate-400">—（人工回复，不经过 LLM）</span>
                      ) : (
                        <code className="rounded bg-blue-50 px-1.5 py-0.5 font-mono text-[11px] text-blue-700">{route.upstream_model}</code>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${MODE_LABELS[route.mode].className}`}>
                        {MODE_LABELS[route.mode].label}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-slate-600">{route.provider_name || "—"}</td>
                    <td className="px-5 py-4 text-right">
                      {!isAdminView && (
                        <button type="button" onClick={() => void doDeleteRoute(route)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <footer className="flex items-center justify-between border-t border-slate-100 px-5 py-3">
              <Pagination page={page} pageSize={50} total={total} onChange={setPage} />
            </footer>
          </section>
        </>
      )}

      {tab === "catalog" && user?.role === "admin" && (
        <>
          <div className="flex justify-end">
            <button type="button" onClick={() => setCatalogFormOpen(true)} className="inline-flex items-center gap-2 rounded-md bg-[#409eff] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#337ecc]">
              <Icon name="plus" className="h-4 w-4" />添加目录项
            </button>
          </div>
          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
                  <th className="px-5 py-3">模型 ID（对外名）</th>
                  <th className="px-4 py-3">归属</th>
                  <th className="px-4 py-3">排序</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {catalog.map((entry) => (
                  <tr key={entry.id} className="text-xs">
                    <td className="px-5 py-3.5 font-mono text-slate-700">{entry.model_id}</td>
                    <td className="px-4 py-3.5 text-slate-600">{entry.owned_by || "—"}</td>
                    <td className="px-4 py-3.5 font-mono text-[11px] text-slate-400">{entry.sort_order}</td>
                    <td className="px-4 py-3.5">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${entry.active ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400"}`}>
                        {entry.active ? "启用" : "停用"}
                      </span>
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <button type="button" onClick={() => void doDeleteCatalog(entry)} className="rounded border border-transparent px-2 py-1.5 text-[11px] text-slate-400 hover:border-red-100 hover:bg-red-50 hover:text-red-500">删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <footer className="border-t border-slate-100 px-5 py-3 text-[10px] text-slate-400">
              共 {catalog.length} 项 · 用户创建路由时「对外模型名」必须来自此目录
            </footer>
          </section>
        </>
      )}

      {createOpen && (
        <Modal title="创建模型路由" description="对外模型名来自管理员目录；LLM 模式的上游模型必须先在供应商同步。" onClose={() => setCreateOpen(false)} width="max-w-2xl">
          <form onSubmit={submitCreate} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">路由名称</span>
              <input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：deepseek-人工中转" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">对外模型名（来自目录）</span>
              <select required value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} className="field-input">
                <option value="">请选择…</option>
                {catalog.filter((entry) => entry.active).map((entry) => (
                  <option key={entry.id} value={entry.model_id}>{entry.model_id}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">模式</span>
              <select value={form.mode} onChange={(event) => setForm({ ...form, mode: event.target.value as RouteMode })} className="field-input">
                {Object.entries(MODE_LABELS).map(([value, meta]) => (
                  <option key={value} value={value}>{meta.label} — {meta.hint}</option>
                ))}
              </select>
            </label>
            {needsUpstream && (
              <>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-slate-600">供应商 <span className="text-red-400">*</span></span>
                  <select required value={form.provider_id} onChange={(event) => setForm({ ...form, provider_id: event.target.value, upstream_model: "" })} className="field-input">
                    <option value="">请选择…</option>
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.id}>{provider.name}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-slate-600">实际上游模型 <span className="text-red-400">*</span></span>
                  <select required value={form.upstream_model} onChange={(event) => setForm({ ...form, upstream_model: event.target.value })} className="field-input" disabled={!form.provider_id}>
                    <option value="">{form.provider_id ? "请先在供应商页同步模型" : "请先选择供应商"}</option>
                    {providerModels.map((model) => (
                      <option key={model.id} value={model.id}>{model.id}</option>
                    ))}
                  </select>
                  {selectedProvider && providerModels.length === 0 && (
                    <p className="mt-1.5 text-[11px] text-amber-500">该供应商还没有同步模型，请先到「供应商」页同步。</p>
                  )}
                </label>
              </>
            )}
            {form.mode !== "llm" && (
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">人工回复超时（秒）</span>
                <input type="number" min={1} max={86400} value={form.human_timeout_seconds} onChange={(event) => setForm({ ...form, human_timeout_seconds: Number(event.target.value) })} className="field-input" />
              </label>
            )}
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setCreateOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "创建中…" : "创建路由"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {catalogFormOpen && (
        <Modal title="添加对外模型目录项" description="用户创建路由时将从目录中选择对外模型名。" onClose={() => setCatalogFormOpen(false)}>
          <form onSubmit={submitCatalog} className="space-y-4 px-6 py-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">模型 ID（对外名）</span>
              <input required value={catalogForm.model_id} onChange={(event) => setCatalogForm({ ...catalogForm, model_id: event.target.value })} placeholder="例如：gpt-5.5" className="field-input" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">归属展示</span>
              <input value={catalogForm.owned_by} onChange={(event) => setCatalogForm({ ...catalogForm, owned_by: event.target.value })} placeholder="例如：openai" className="field-input" />
            </label>
            <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
              <button type="button" onClick={() => setCatalogFormOpen(false)} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
              <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
                {submitting ? "添加中…" : "添加"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

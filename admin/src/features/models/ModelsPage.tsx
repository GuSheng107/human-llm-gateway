import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CAPABILITY_LABELS,
  ENDPOINT_LABELS,
  deleteFakeModel,
  listAllFakeModels,
  listFakeModels,
  listModelGroups,
  updateFakeModel,
} from "../../api/models";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { useAuth } from "../auth/AuthContext";
import type { FakeModel, ModelGroup } from "../../types/gateway";
import { ModelEditModal } from "./ModelEditModal";
import { ModelsGroupsDrawer } from "./ModelsGroupsDrawer";
import { MODEL_PAGE_SIZES, type ModelPageSize } from "./modelPagination";

const VIEW_STORAGE_KEY = "hlg_models_view";

type ViewMode = "grid" | "table";

const ENDPOINT_GROUP_LABELS: Record<string, string> = {
  "": "全部",
  openai_chat: "OpenAI",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic",
};

function formatContext(value: number | null): string {
  if (!value) return "-";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0)}M`;
  if (value >= 1000) return `${Math.round(value / 1000)}k`;
  return String(value);
}

function ModelLogo({ model }: { model: FakeModel }) {
  const [failed, setFailed] = useState(false);
  const letter = (model.display_name || model.model_id).charAt(0).toUpperCase();
  if (model.logo_url && !failed) {
    return (
      <img
        src={model.logo_url}
        alt={model.owned_by}
        onError={() => setFailed(true)}
        className="h-10 w-10 shrink-0 rounded-lg object-cover"
      />
    );
  }
  return (
    <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-primary/10 to-violet-500/10 text-sm font-bold text-primary">
      {letter}
    </div>
  );
}

export function ModelsPage() {
  const { user } = useAuth();
  const [models, setModels] = useState<FakeModel[]>([]);
  const [pageModels, setPageModels] = useState<FakeModel[]>([]);
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const pageRequest = useRef(0);
  const isAdmin = user?.role === "admin";

  // 筛选状态
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("");
  const [group, setGroup] = useState("");
  const [endpointType, setEndpointType] = useState("");
  const [showDisabled, setShowDisabled] = useState(false);

  const [view, setView] = useState<ViewMode>(() =>
    localStorage.getItem(VIEW_STORAGE_KEY) === "table" ? "table" : "grid",
  );

  const [editing, setEditing] = useState<FakeModel | null>(null);
  const [creating, setCreating] = useState(false);
  const [groupsOpen, setGroupsOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<ModelPageSize>(20);

  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    try {
      const [modelPage, groupPage] = await Promise.all([
        listAllFakeModels({ include_disabled: true }),
        listModelGroups(),
      ]);
      setModels(modelPage);
      setGroups(groupPage.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  const loadPage = useCallback(async () => {
    const requestId = ++pageRequest.current;
    setPageLoading(true);
    try {
      const result = await listFakeModels(
        {
          search,
          provider,
          group_id: group,
          endpoint_type: endpointType,
          include_disabled: showDisabled,
        },
        page,
        pageSize,
      );
      if (requestId !== pageRequest.current) return;
      setPageModels(result.items);
      setTotal(result.total);
      setError("");
      const lastPage = Math.max(1, Math.ceil(result.total / pageSize));
      if (page > lastPage) setPage(lastPage);
    } catch (caught) {
      if (requestId === pageRequest.current) {
        setError(caught instanceof Error ? caught.message : "加载失败");
      }
    } finally {
      if (requestId === pageRequest.current) setPageLoading(false);
    }
  }, [endpointType, group, page, pageSize, provider, search, showDisabled]);

  const refresh = useCallback(async () => {
    await Promise.all([loadCatalog(), loadPage()]);
  }, [loadCatalog, loadPage]);

  useEffect(() => void loadCatalog(), [loadCatalog]);
  useEffect(() => void loadPage(), [loadPage]);

  // 聚合筛选维度
  const providers = useMemo(() => {
    const counter = new Map<string, number>();
    for (const model of models) {
      counter.set(model.owned_by, (counter.get(model.owned_by) ?? 0) + 1);
    }
    return [...counter.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [models]);

  const loading = pageLoading || (catalogLoading && models.length === 0);

  const copyModelId = async (model: FakeModel) => {
    await copyText(model.model_id, "模型 ID");
  };

  const toggleModel = async (model: FakeModel) => {
    try {
      await updateFakeModel(model.id, { enabled: !model.is_enabled });
      notify(model.is_enabled ? "模型已停用" : "模型已启用");
      await refresh();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败");
    }
  };

  const removeModel = async (model: FakeModel) => {
    if (!window.confirm(`确认删除模型「${model.model_id}」？`)) return;
    try {
      await deleteFakeModel(model.id);
      notify("模型已删除");
      await refresh();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  const switchView = (mode: ViewMode) => {
    setView(mode);
    localStorage.setItem(VIEW_STORAGE_KEY, mode);
  };

  const filterChips = (
    values: [string, number][],
    active: string,
    onSelect: (value: string) => void,
    labelOf: (value: string) => string,
  ) => (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={() => {
          setPage(1);
          onSelect("");
        }}
        className={`rounded-full px-2.5 py-1 text-xs transition ${
          active === "" ? "bg-primary text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
        }`}
      >
        全部
      </button>
      {values.map(([value, count]) => (
        <button
          key={value}
          type="button"
          onClick={() => {
            setPage(1);
            onSelect(value);
          }}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition ${
            active === value
              ? "bg-primary text-white"
              : "bg-slate-100 text-slate-500 hover:bg-slate-200"
          }`}
        >
          {labelOf(value)}
          <span className={active === value ? "text-white/70" : "text-slate-400"}>{count}</span>
        </button>
      ))}
    </div>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="模型广场"
        dismissId="models"
        actions={
          <>
            <Button variant="ghost" onClick={() => setGroupsOpen(true)}>
              <Icon name="settings" className="h-4 w-4" />
              管理分组
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Icon name="plus" className="h-4 w-4" />
              新建模型
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      <div className="rounded-xl bg-gradient-to-r from-violet-600 via-indigo-600 to-blue-600 px-6 py-5 text-white shadow-card">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
          <div>
            <div className="text-2xl font-bold">{models.length}</div>
            <div className="text-xs text-white/70">模型</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{groups.length}</div>
            <div className="text-xs text-white/70">分组</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{providers.length}</div>
            <div className="text-xs text-white/70">供应商</div>
          </div>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-white/80">
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(event) => {
                setPage(1);
                setShowDisabled(event.target.checked);
              }}
              className="accent-white"
            />
            显示已停用
          </label>
        </div>
      </div>

      <div className="flex gap-5">
        {/* 左侧筛选栏 */}
        <aside className="hidden w-52 shrink-0 space-y-5 lg:block">
          <Card className="p-4">
            <h3 className="mb-2 text-xs font-semibold text-slate-700">供应商</h3>
            {filterChips(providers, provider, setProvider, (value) => value)}
          </Card>
          <Card className="p-4">
            <h3 className="mb-2 text-xs font-semibold text-slate-700">模型分组</h3>
            {filterChips(
              groups.map((item) => [item.id, item.model_ids.length] as [string, number]),
              group,
              setGroup,
              (value) => groups.find((item) => item.id === value)?.name ?? value,
            )}
          </Card>
          <Card className="p-4">
            <h3 className="mb-2 text-xs font-semibold text-slate-700">端点类型</h3>
            {filterChips(
              Object.keys(ENDPOINT_GROUP_LABELS)
                .filter((key) => key !== "" && models.some((model) => model.endpoint_type === key))
                .map((key) => [key, models.filter((m) => m.endpoint_type === key).length] as [
                  string,
                  number,
                ]),
              endpointType,
              setEndpointType,
              (value) => ENDPOINT_GROUP_LABELS[value],
            )}
          </Card>
        </aside>

        {/* 右侧主区 */}
        <div className="min-w-0 flex-1 space-y-4">
          <Card className="p-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <Icon
                  name="search"
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-300"
                />
                <input
                  value={search}
                  onChange={(event) => {
                    setPage(1);
                    setSearch(event.target.value);
                  }}
                  className="field-input pl-9"
                  placeholder="搜索模型 ID、显示名或描述"
                />
              </div>
              <div className="flex rounded-md border border-slate-200 p-0.5">
                <button
                  type="button"
                  onClick={() => switchView("grid")}
                  className={`rounded px-2.5 py-1 text-xs transition ${
                    view === "grid" ? "bg-primary text-white" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  网格
                </button>
                <button
                  type="button"
                  onClick={() => switchView("table")}
                  className={`rounded px-2.5 py-1 text-xs transition ${
                    view === "table" ? "bg-primary text-white" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  表格
                </button>
              </div>
            </div>
            <div className="mt-2 flex items-center justify-between px-1 text-[11px] text-slate-400">
              <span>共 {total} 个模型</span>
              <span>第 {page} 页</span>
            </div>
          </Card>

          {loading && (
            <Card className="p-12 text-center text-xs text-slate-400">加载中…</Card>
          )}

          {!loading && total === 0 && (
            <Card className="p-12 text-center text-xs text-slate-400">
              没有符合条件的模型，试试调整筛选
            </Card>
          )}

          {!loading && view === "grid" && total > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {pageModels.map((model) => (
                <Card key={model.id} className="group flex flex-col p-4">
                  <div className="flex items-start justify-between">
                    <div className="flex min-w-0 items-center gap-3">
                      <ModelLogo model={model} />
                      <div className="min-w-0">
                        <div
                          className="truncate font-mono text-sm font-semibold text-primary"
                          title={model.model_id}
                        >
                          {model.model_id}
                        </div>
                        <div className="truncate text-[11px] text-slate-400">
                          {model.display_name || model.owned_by}
                        </div>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1 opacity-0 transition group-hover:opacity-100">
                      <button
                        type="button"
                        aria-label="复制模型 ID"
                        onClick={() => void copyModelId(model)}
                        className="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                      >
                        <Icon name="copy" className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  {model.capabilities.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {model.capabilities.slice(0, 5).map((capability) => (
                        <span
                          key={capability}
                          className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-600"
                        >
                          {CAPABILITY_LABELS[capability] ?? capability}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className="mt-3 space-y-1.5">
                    {model.context_window && (
                      <div className="flex items-baseline justify-between text-xs">
                        <span className="text-slate-400">上下文</span>
                        <span className="font-mono text-slate-700">
                          {formatContext(model.context_window)}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="mt-auto flex items-center justify-between border-t border-slate-100 pt-3">
                    <div className="flex items-center gap-1.5">
                      {!model.is_enabled && (
                        <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] text-red-500">
                          已停用
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <button
                        type="button"
                        onClick={() => void toggleModel(model)}
                        className="text-primary"
                      >
                        {model.is_enabled ? "停用" : "启用"}
                      </button>
                      <button type="button" onClick={() => setEditing(model)} className="text-primary">
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeModel(model)}
                        className="text-red-500"
                      >
                        删除
                      </button>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}

          {!loading && view === "table" && total > 0 && (
            <Card>
              <div className="overflow-x-auto">
                <table className="min-w-[820px] w-full text-left text-xs">
                  <thead className="bg-slate-50 text-slate-400">
                    <tr>
                      <th className="px-4 py-3 font-medium">model_id</th>
                      <th className="px-4 py-3 font-medium">显示名</th>
                      <th className="px-4 py-3 font-medium">端点</th>
                      <th className="px-4 py-3 font-medium">上下文</th>
                      <th className="px-4 py-3 font-medium">能力</th>
                      <th className="px-4 py-3 font-medium">状态</th>
                      <th className="px-4 py-3 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {pageModels.map((model) => (
                      <tr key={model.id} className="group hover:bg-slate-50/60">
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={() => void copyModelId(model)}
                            className="font-mono text-primary hover:underline"
                            title="点击复制"
                          >
                            {model.model_id}
                          </button>
                        </td>
                        <td className="px-4 py-3 text-slate-500">{model.display_name ?? "-"}</td>
                        <td className="px-4 py-3 text-slate-500">
                          {ENDPOINT_LABELS[model.endpoint_type] ?? model.endpoint_type}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-500">
                          {formatContext(model.context_window)}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex max-w-40 flex-wrap gap-1">
                            {model.capabilities.slice(0, 3).map((capability) => (
                              <span
                                key={capability}
                                className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-600"
                              >
                                {CAPABILITY_LABELS[capability] ?? capability}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={model.is_enabled ? "active" : "inactive"} />
                        </td>
                        <td className="space-x-3 px-4 py-3 text-right">
                          <button
                            type="button"
                            onClick={() => void toggleModel(model)}
                            className="text-primary"
                          >
                            {model.is_enabled ? "停用" : "启用"}
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditing(model)}
                            className="text-primary"
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            onClick={() => void removeModel(model)}
                            className="text-red-500"
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          {!loading && total > 0 && (
            <Card className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <label className="flex items-center gap-2 text-xs text-slate-500">
                每页
                <select
                  value={pageSize}
                  onChange={(event) => {
                    setPage(1);
                    setPageSize(Number(event.target.value) as ModelPageSize);
                  }}
                  className="field-input h-8 w-20 py-1"
                  aria-label="每页模型数"
                >
                  {MODEL_PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </label>
              <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage} />
            </Card>
          )}
        </div>
      </div>

      {(creating || editing) && (
        <ModelEditModal
          model={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={refresh}
        />
      )}

      {groupsOpen && user && (
        <ModelsGroupsDrawer
          groups={groups}
          models={models}
          currentUserId={user.id}
          isAdmin={isAdmin}
          onClose={() => setGroupsOpen(false)}
          onChanged={refresh}
        />
      )}
    </div>
  );
}

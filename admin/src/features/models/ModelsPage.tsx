import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BILLING_LABELS,
  CAPABILITY_LABELS,
  ENDPOINT_LABELS,
  listFakeModels,
  listModelGroups,
  createModelGroup,
  deleteModelGroup,
  deleteFakeModel,
  replaceGroupMembers,
  updateFakeModel,
} from "../../api/models";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { useAuth } from "../auth/AuthContext";
import type { FakeModel, ModelGroup } from "../../types/gateway";
import { ModelEditModal } from "./ModelEditModal";

const VIEW_STORAGE_KEY = "hlg_models_view";

type ViewMode = "grid" | "table";

const ENDPOINT_GROUP_LABELS: Record<string, string> = {
  "": "全部",
  openai_chat: "OpenAI",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic",
};

const BILLING_GROUP_LABELS: Record<string, string> = {
  "": "全部",
  pay_as_you_go: "按量计费",
  subscription: "订阅",
  free: "免费",
  dynamic: "动态计费",
};

function formatPrice(value: number | null): string {
  if (value === null || value === undefined) return "";
  return Number.isInteger(value) ? String(value) : String(value);
}

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

function PriceRow({ label, value }: { label: string; value: number | null }) {
  if (value === null || value === undefined) return null;
  return (
    <div className="flex items-baseline justify-between text-xs">
      <span className="text-slate-400">{label}</span>
      <span className="font-mono text-slate-700">
        {formatPrice(value)}
        <span className="ml-0.5 text-[10px] text-slate-400">元/1M</span>
      </span>
    </div>
  );
}

export function ModelsPage() {
  const { user } = useAuth();
  const [models, setModels] = useState<FakeModel[]>([]);
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const isAdmin = user?.role === "admin";

  // 筛选状态
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("");
  const [group, setGroup] = useState("");
  const [billingTier, setBillingTier] = useState("");
  const [endpointType, setEndpointType] = useState("");
  const [tag, setTag] = useState("");
  const [showDisabled, setShowDisabled] = useState(false);

  const [view, setView] = useState<ViewMode>(() =>
    localStorage.getItem(VIEW_STORAGE_KEY) === "table" ? "table" : "grid",
  );

  const [editing, setEditing] = useState<FakeModel | null>(null);
  const [creating, setCreating] = useState(false);
  const [groupForm, setGroupForm] = useState<{ name: string; description: string } | null>(null);
  const [editingGroup, setEditingGroup] = useState<ModelGroup | null>(null);
  const [selectedMembers, setSelectedMembers] = useState<number[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [modelPage, groupPage] = await Promise.all([
        listFakeModels({ include_disabled: true }),
        listModelGroups(),
      ]);
      setModels(modelPage.items);
      setGroups(groupPage.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  // 聚合筛选维度
  const providers = useMemo(() => {
    const counter = new Map<string, number>();
    for (const model of models) {
      counter.set(model.owned_by, (counter.get(model.owned_by) ?? 0) + 1);
    }
    return [...counter.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [models]);

  const tags = useMemo(() => {
    const counter = new Map<string, number>();
    for (const model of models) {
      for (const item of model.tags) {
        counter.set(item, (counter.get(item) ?? 0) + 1);
      }
    }
    return [...counter.entries()].sort((a, b) => b[1] - a[1]);
  }, [models]);

  // 前端综合筛选（数据量小，内存过滤即可）
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const groupModelIds = group
      ? new Set(groups.find((item) => item.name === group)?.model_ids ?? [])
      : null;
    return models.filter((model) => {
      if (!showDisabled && !model.is_enabled) return false;
      if (provider && model.owned_by !== provider) return false;
      if (groupModelIds && !groupModelIds.has(model.model_id)) return false;
      if (billingTier && model.billing_tier !== billingTier) return false;
      if (endpointType && model.endpoint_type !== endpointType) return false;
      if (tag && !model.tags.includes(tag)) return false;
      if (term) {
        const haystack = [
          model.model_id,
          model.display_name ?? "",
          model.description ?? "",
          ...model.tags,
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }, [models, search, provider, group, billingTier, endpointType, tag, showDisabled, groups]);

  const priceStats = useMemo(() => {
    const withPrice = filtered.filter((model) => model.input_price_per_million !== null);
    const inputs = withPrice.map((model) => model.input_price_per_million ?? 0);
    return {
      providers: new Set(models.map((model) => model.owned_by)).size,
      min: inputs.length ? Math.min(...inputs) : null,
      max: inputs.length ? Math.max(...inputs) : null,
      cachedCount: models.filter((model) => model.cached_input_price_per_million !== null).length,
    };
  }, [filtered, models]);

  const copyModelId = async (model: FakeModel) => {
    await navigator.clipboard.writeText(model.model_id);
    notify(`已复制 ${model.model_id}`);
  };

  const toggleModel = async (model: FakeModel) => {
    try {
      await updateFakeModel(model.id, { enabled: !model.is_enabled });
      notify(model.is_enabled ? "模型已停用" : "模型已启用");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败");
    }
  };

  const removeModel = async (model: FakeModel) => {
    if (!window.confirm(`确认删除模型「${model.model_id}」？`)) return;
    try {
      await deleteFakeModel(model.id);
      notify("模型已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  const submitGroup = async () => {
    if (!groupForm) return;
    try {
      await createModelGroup({
        name: groupForm.name.trim(),
        description: groupForm.description.trim() || null,
      });
      notify("分组已创建");
      setGroupForm(null);
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "创建失败");
    }
  };

  const saveMembers = async () => {
    if (!editingGroup) return;
    try {
      await replaceGroupMembers(editingGroup.id, selectedMembers);
      notify("分组成员已更新");
      setEditingGroup(null);
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "更新失败");
    }
  };

  const removeGroup = async (groupToRemove: ModelGroup) => {
    if (!window.confirm(`确认删除分组「${groupToRemove.name}」？被启用的 Key 引用时会失败。`))
      return;
    try {
      await deleteModelGroup(groupToRemove.id);
      notify("分组已删除");
      await load();
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
        onClick={() => onSelect("")}
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
          onClick={() => onSelect(value)}
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
        description={
          isAdmin
            ? "维护系统模型与分组；价格与能力信息仅用于展示"
            : "系统模型所有人都能用，你自己建的模型只有你能用"
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => setGroupForm({ name: "", description: "" })}>
              <Icon name="plus" className="h-4 w-4" />
              新建分组
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Icon name="plus" className="h-4 w-4" />
              新建模型
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      {/* 顶部统计 Banner（参考 newapi 模型广场） */}
      <div className="rounded-xl bg-gradient-to-r from-violet-600 via-indigo-600 to-blue-600 px-6 py-5 text-white shadow-card">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
          <div>
            <div className="text-2xl font-bold">{models.length}</div>
            <div className="text-xs text-white/70">可用模型</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{priceStats.providers}</div>
            <div className="text-xs text-white/70">供应商</div>
          </div>
          <div>
            <div className="text-2xl font-bold">
              {priceStats.min !== null ? `¥${formatPrice(priceStats.min)}` : "-"}
              {priceStats.max !== null && priceStats.min !== priceStats.max
                ? ` ~ ¥${formatPrice(priceStats.max)}`
                : ""}
            </div>
            <div className="text-xs text-white/70">输入价区间（元 / 1M tokens）</div>
          </div>
          <div>
            <div className="text-2xl font-bold">{priceStats.cachedCount}</div>
            <div className="text-xs text-white/70">支持缓存计价</div>
          </div>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-white/80">
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(event) => setShowDisabled(event.target.checked)}
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
              groups.map((item) => [item.name, item.model_ids.length] as [string, number]),
              group,
              setGroup,
              (value) => value,
            )}
          </Card>
          <Card className="p-4">
            <h3 className="mb-2 text-xs font-semibold text-slate-700">端点类型</h3>
            {filterChips(
              Object.keys(ENDPOINT_GROUP_LABELS)
                .filter((key) => key === "" || models.some((model) => model.endpoint_type === key))
                .map((key) => [
                  key,
                  key === "" ? models.length : models.filter((m) => m.endpoint_type === key).length,
                ] as [string, number]),
              endpointType,
              setEndpointType,
              (value) => ENDPOINT_GROUP_LABELS[value],
            )}
          </Card>
          <Card className="p-4">
            <h3 className="mb-2 text-xs font-semibold text-slate-700">计费类型</h3>
            {filterChips(
              Object.keys(BILLING_GROUP_LABELS)
                .filter((key) => key === "" || models.some((model) => model.billing_tier === key))
                .map((key) => [
                  key,
                  key === "" ? models.length : models.filter((m) => m.billing_tier === key).length,
                ] as [string, number]),
              billingTier,
              setBillingTier,
              (value) => BILLING_GROUP_LABELS[value],
            )}
          </Card>
          {tags.length > 0 && (
            <Card className="p-4">
              <h3 className="mb-2 text-xs font-semibold text-slate-700">标签</h3>
              {filterChips(tags, tag, setTag, (value) => value)}
            </Card>
          )}
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
                  onChange={(event) => setSearch(event.target.value)}
                  className="field-input pl-9"
                  placeholder="搜索模型 ID、显示名、描述或标签"
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
              <span>共 {filtered.length} 个模型</span>
              <span>价格单位：元 / 1M tokens</span>
            </div>
          </Card>

          {loading && (
            <Card className="p-12 text-center text-xs text-slate-400">加载中…</Card>
          )}

          {!loading && filtered.length === 0 && (
            <Card className="p-12 text-center text-xs text-slate-400">
              没有符合条件的模型，试试调整筛选
            </Card>
          )}

          {!loading && view === "grid" && filtered.length > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filtered.map((model) => (
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
                    <PriceRow label="输入" value={model.input_price_per_million} />
                    <PriceRow label="输出" value={model.output_price_per_million} />
                    <PriceRow label="缓存读" value={model.cached_input_price_per_million} />
                    <PriceRow label="缓存写" value={model.cached_write_price_per_million} />
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
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                        {BILLING_LABELS[model.billing_tier] ?? model.billing_tier}
                      </span>
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

          {!loading && view === "table" && filtered.length > 0 && (
            <Card>
              <div className="overflow-x-auto">
                <table className="min-w-[1080px] w-full text-left text-xs">
                  <thead className="bg-slate-50 text-slate-400">
                    <tr>
                      <th className="px-4 py-3 font-medium">model_id</th>
                      <th className="px-4 py-3 font-medium">显示名</th>
                      <th className="px-4 py-3 font-medium">端点</th>
                      <th className="px-4 py-3 font-medium">上下文</th>
                      <th className="px-4 py-3 font-medium">输入价</th>
                      <th className="px-4 py-3 font-medium">输出价</th>
                      <th className="px-4 py-3 font-medium">缓存读</th>
                      <th className="px-4 py-3 font-medium">能力</th>
                      <th className="px-4 py-3 font-medium">标签</th>
                      <th className="px-4 py-3 font-medium">状态</th>
                      <th className="px-4 py-3 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filtered.map((model) => (
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
                        <td className="px-4 py-3 font-mono text-slate-500">
                          {formatPrice(model.input_price_per_million) || "-"}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-500">
                          {formatPrice(model.output_price_per_million) || "-"}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-500">
                          {formatPrice(model.cached_input_price_per_million) || "-"}
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
                        <td className="max-w-32 truncate px-4 py-3 text-slate-400">
                          {model.tags.join("、") || "-"}
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
        </div>
      </div>

      {/* 模型分组管理（沿用原能力，收纳到底部） */}
      <Card>
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-700">模型分组</h2>
          <span className="text-xs text-slate-400">Key 可按分组批量选用模型</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[720px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">成员模型</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {groups.map((item) => (
                <tr key={item.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{item.name}</td>
                  <td className="max-w-sm truncate px-4 py-3 text-slate-500">
                    {item.model_ids.length ? item.model_ids.join("、") : "未选择成员"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.is_enabled ? "active" : "inactive"} />
                  </td>
                  <td className="space-x-3 px-4 py-3 text-right">
                    <button
                      onClick={() => {
                        setEditingGroup(item);
                        setSelectedMembers(
                          item.model_ids
                            .map((modelId) => models.find((m) => m.model_id === modelId)?.id)
                            .filter((value): value is string => Boolean(value))
                            .map(Number),
                        );
                      }}
                      className="text-primary"
                    >
                      成员
                    </button>
                    <button onClick={() => void removeGroup(item)} className="text-red-500">
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && groups.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-12 text-center text-slate-400">
                    暂无分组
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {(creating || editing) && (
        <ModelEditModal
          model={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={load}
        />
      )}

      {groupForm && (
        <Modal
          title="新建模型分组"
          description="把常用模型归成一组，方便 Key 一起选用"
          onClose={() => setGroupForm(null)}
        >
          <div className="space-y-4 p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">分组名称</span>
              <input
                value={groupForm.name}
                onChange={(event) =>
                  setGroupForm((previous) => previous && { ...previous, name: event.target.value })
                }
                className="field-input"
                placeholder="例如：常用模型"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">说明</span>
              <input
                value={groupForm.description}
                onChange={(event) =>
                  setGroupForm(
                    (previous) => previous && { ...previous, description: event.target.value },
                  )
                }
                className="field-input"
                placeholder="可留空"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setGroupForm(null)}>
                取消
              </Button>
              <Button onClick={() => void submitGroup()} disabled={!groupForm.name.trim()}>
                <Icon name="check" className="h-4 w-4" />
                创建
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {editingGroup && (
        <Modal
          title={`分组成员 · ${editingGroup.name}`}
          description="勾选组内模型，保存后替换。"
          onClose={() => setEditingGroup(null)}
        >
          <div className="max-h-80 space-y-2 overflow-y-auto p-6">
            {models.map((model) => (
              <label
                key={model.id}
                className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-600"
              >
                <input
                  type="checkbox"
                  checked={selectedMembers.includes(Number(model.id))}
                  onChange={(event) =>
                    setSelectedMembers((previous) =>
                      event.target.checked
                        ? [...previous, Number(model.id)]
                        : previous.filter((value) => value !== Number(model.id)),
                    )
                  }
                />
                <span className="font-mono text-slate-700">{model.model_id}</span>
                <span className="text-slate-400">
                  {model.scope === "system" ? "系统" : "私有"}
                </span>
              </label>
            ))}
          </div>
          <div className="flex justify-end gap-2 border-t border-slate-100 p-4">
            <Button variant="ghost" onClick={() => setEditingGroup(null)}>
              取消
            </Button>
            <Button onClick={() => void saveMembers()}>保存</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

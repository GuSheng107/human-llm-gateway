import { useCallback, useEffect, useState } from "react";
import {
  createFakeModel,
  createModelGroup,
  deleteFakeModel,
  deleteModelGroup,
  listFakeModels,
  listModelGroups,
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

export function ModelsPage() {
  const { user } = useAuth();
  const [models, setModels] = useState<FakeModel[]>([]);
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modelForm, setModelForm] = useState<{ model_id: string; display_name: string } | null>(null);
  const [groupForm, setGroupForm] = useState<{ name: string; description: string } | null>(null);
  const [editingGroup, setEditingGroup] = useState<ModelGroup | null>(null);
  const [selectedMembers, setSelectedMembers] = useState<number[]>([]);
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [modelPage, groupPage] = await Promise.all([listFakeModels(), listModelGroups()]);
      setModels(modelPage.items);
      setGroups(groupPage.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  const submitModel = async () => {
    if (!modelForm) return;
    try {
      await createFakeModel({
        model_id: modelForm.model_id.trim(),
        display_name: modelForm.display_name.trim() || null,
      });
      notify("模型已创建");
      setModelForm(null);
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "创建失败");
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

  const openMembers = (group: ModelGroup) => {
    setEditingGroup(group);
    setSelectedMembers(
      group.model_ids
        .map((modelId) => models.find((item) => item.model_id === modelId)?.id)
        .filter((value): value is string => Boolean(value))
        .map(Number),
    );
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

  const removeGroup = async (group: ModelGroup) => {
    if (!window.confirm(`确认删除分组「${group.name}」？被启用的 Key 引用时会失败。`)) return;
    try {
      await deleteModelGroup(group.id);
      notify("分组已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="模型目录"
        description={
          isAdmin
            ? "维护系统模型与分组"
            : "系统模型所有人都能用，你自己建的模型只有你能用"
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => setGroupForm({ name: "", description: "" })}>
              <Icon name="plus" className="h-4 w-4" />
              新建分组
            </Button>
            <Button onClick={() => setModelForm({ model_id: "", display_name: "" })}>
              <Icon name="plus" className="h-4 w-4" />
              新建模型
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}

      <Card>
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-700">Fake Model</h2>
          <span className="text-xs text-slate-400">共 {models.length} 个</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[720px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">model_id</th>
                <th className="px-4 py-3 font-medium">显示名</th>
                <th className="px-4 py-3 font-medium">范围</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {models.map((model) => (
                <tr key={model.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-mono text-slate-700">{model.model_id}</td>
                  <td className="px-4 py-3 text-slate-500">{model.display_name ?? "-"}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {model.scope === "system" ? "系统" : "私有"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={model.is_enabled ? "active" : "inactive"} />
                  </td>
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => void toggleModel(model)} className="text-primary">
                      {model.is_enabled ? "停用" : "启用"}
                    </button>
                    <button onClick={() => void removeModel(model)} className="text-red-500">
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && models.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-400">
                    暂无模型
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

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
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {groups.map((group) => (
                <tr key={group.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{group.name}</td>
                  <td className="max-w-sm truncate px-4 py-3 text-slate-500">
                    {group.model_ids.length ? group.model_ids.join("、") : "未选择成员"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={group.is_enabled ? "active" : "inactive"} />
                  </td>
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => openMembers(group)} className="text-primary">
                      成员
                    </button>
                    <button onClick={() => void removeGroup(group)} className="text-red-500">
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

      {modelForm && (
        <Modal
          title="新建 Fake Model"
          description={isAdmin ? "创建后所有人都能用" : "创建后只有你能用"}
          onClose={() => setModelForm(null)}
        >
          <div className="space-y-4 p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">model_id</span>
              <input
                value={modelForm.model_id}
                onChange={(event) =>
                  setModelForm((previous) => previous && { ...previous, model_id: event.target.value })
                }
                className="field-input"
                placeholder="例如：human-gateway-plus"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
              <input
                value={modelForm.display_name}
                onChange={(event) =>
                  setModelForm(
                    (previous) => previous && { ...previous, display_name: event.target.value },
                  )
                }
                className="field-input"
                placeholder="可留空"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModelForm(null)}>
                取消
              </Button>
              <Button
                onClick={() => void submitModel()}
                disabled={!modelForm.model_id.trim()}
                loading={loading}
              >
                <Icon name="check" className="h-4 w-4" />
                创建
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {groupForm && (
        <Modal title="新建模型分组" description="把常用模型归成一组，方便 Key 一起选用" onClose={() => setGroupForm(null)}>
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

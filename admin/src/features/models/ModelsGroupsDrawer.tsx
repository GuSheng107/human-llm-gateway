import { useEffect, useMemo, useState } from "react";
import {
  createModelGroup,
  deleteModelGroup,
  replaceGroupMembers,
  updateModelGroup,
} from "../../api/models";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Drawer } from "../../components/feedback/Drawer";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { FakeModel, ModelGroup } from "../../types/gateway";

interface Props {
  groups: ModelGroup[];
  models: FakeModel[];
  currentUserId: string;
  isAdmin: boolean;
  onClose: () => void;
  onChanged: () => Promise<void>;
}

interface GroupForm {
  name: string;
  description: string;
  enabled: boolean;
  memberIds: number[];
}

const emptyForm = (): GroupForm => ({
  name: "",
  description: "",
  enabled: true,
  memberIds: [],
});

export function ModelsGroupsDrawer({
  groups,
  models,
  currentUserId,
  isAdmin,
  onClose,
  onChanged,
}: Props) {
  const manageable = useMemo(
    () => (isAdmin ? groups : groups.filter((group) => group.owner_user_id === currentUserId)),
    [groups, currentUserId, isAdmin],
  );
  const [selectedId, setSelectedId] = useState<string | null>(manageable[0]?.id ?? null);
  const [form, setForm] = useState<GroupForm>(emptyForm);
  const [memberSearch, setMemberSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const selected = manageable.find((group) => group.id === selectedId) ?? null;

  const availableModels = useMemo(() => {
    const ownerId = selected?.owner_user_id ?? currentUserId;
    const term = memberSearch.trim().toLowerCase();
    return models.filter((model) => {
      if (model.scope === "private" && model.owner_user_id !== ownerId) return false;
      if (!term) return true;
      return `${model.model_id} ${model.display_name ?? ""}`.toLowerCase().includes(term);
    });
  }, [currentUserId, memberSearch, models, selected?.owner_user_id]);

  useEffect(() => {
    if (!selected) {
      setForm(emptyForm());
      return;
    }
    setForm({
      name: selected.name,
      description: selected.description ?? "",
      enabled: selected.is_enabled,
      memberIds: selected.model_ids
        .map((modelId) => models.find((model) => model.model_id === modelId)?.id)
        .filter((id): id is string => Boolean(id))
        .map(Number),
    });
  }, [models, selected]);

  const choose = (group: ModelGroup | null) => {
    setSelectedId(group?.id ?? null);
    setMemberSearch("");
  };

  const save = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      let groupId = selected?.id;
      if (selected) {
        await updateModelGroup(selected.id, {
          name: form.name.trim(),
          description: form.description.trim() || null,
          enabled: form.enabled,
        });
      } else {
        const created = await createModelGroup({
          name: form.name.trim(),
          description: form.description.trim() || null,
          enabled: form.enabled,
        });
        groupId = created.id;
      }
      await replaceGroupMembers(groupId!, form.memberIds);
      notify(selected ? "模型分组已更新" : "模型分组已创建", "success");
      await onChanged();
      setSelectedId(groupId!);
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!selected || !(await confirmAction({ message: `确认删除分组「${selected.name}」？` }))) return;
    setSaving(true);
    try {
      await deleteModelGroup(selected.id);
      notify("模型分组已删除", "success");
      setSelectedId(null);
      await onChanged();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败", "error");
    } finally {
      setSaving(false);
    }
  };

  const toggleMember = (modelId: number, checked: boolean) => {
    setForm((current) => ({
      ...current,
      memberIds: checked
        ? [...new Set([...current.memberIds, modelId])]
        : current.memberIds.filter((id) => id !== modelId),
    }));
  };

  return (
    <Drawer
      title="管理模型分组"
      description="创建、改名、启停分组并维护成员模型"
      onClose={onClose}
      width="max-w-4xl"
      side="left"
    >
      <div className="grid min-h-full md:grid-cols-[15rem_1fr]">
        <div className="border-b border-slate-200 bg-slate-50 p-3 md:border-b-0 md:border-r">
          <Button className="mb-3 w-full" onClick={() => choose(null)}>
            <Icon name="plus" className="h-4 w-4" />
            新建分组
          </Button>
          <div className="space-y-1">
            {manageable.map((group) => (
              <button
                key={group.id}
                type="button"
                onClick={() => choose(group)}
                className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-xs transition ${
                  selectedId === group.id
                    ? "bg-white font-medium text-primary shadow-sm"
                    : "text-slate-600 hover:bg-white/70"
                }`}
              >
                <span className="truncate">{group.name}</span>
                <span className="ml-2 text-[10px] text-slate-400">{group.model_ids.length}</span>
              </button>
            ))}
            {manageable.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-slate-400">暂无可管理分组</p>
            )}
          </div>
        </div>

        <div className="space-y-5 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700">
              {selected ? "编辑分组" : "新建分组"}
            </h3>
            {selected && <StatusBadge status={form.enabled ? "active" : "inactive"} />}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">分组名称</span>
              <input
                value={form.name}
                maxLength={100}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                className="field-input"
              />
            </label>
            <label className="flex items-end gap-2 pb-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              />
              启用此分组
            </label>
          </div>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">说明</span>
            <textarea
              value={form.description}
              maxLength={500}
              rows={2}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              className="field-input"
            />
          </label>
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-xs font-medium text-slate-600">
                成员模型（已选 {form.memberIds.length}）
              </span>
              <input
                value={memberSearch}
                onChange={(event) => setMemberSearch(event.target.value)}
                className="field-input max-w-56"
                placeholder="搜索模型"
              />
            </div>
            <div className="max-h-72 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
              {availableModels.map((model) => (
                <label key={model.id} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <input
                    type="checkbox"
                    checked={form.memberIds.includes(Number(model.id))}
                    onChange={(event) => toggleMember(Number(model.id), event.target.checked)}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-slate-700">
                    {model.model_id}
                  </span>
                  <span className="text-slate-400">
                    {model.scope === "system" ? "系统" : "私有"}
                  </span>
                </label>
              ))}
              {availableModels.length === 0 && (
                <p className="p-8 text-center text-xs text-slate-400">没有可选模型</p>
              )}
            </div>
          </div>
          <div className="flex justify-between border-t border-slate-100 pt-4">
            <div>
              {selected && (
                <Button variant="danger" onClick={() => void remove()} disabled={saving}>
                  删除分组
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>取消</Button>
              <Button onClick={() => void save()} loading={saving} disabled={!form.name.trim()}>
                保存
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Drawer>
  );
}

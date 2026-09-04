import { useEffect, useMemo, useState } from "react";
import {
  createModelGroup,
  deleteModelGroup,
  updateModelGroup,
} from "../../api/models";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Drawer } from "../../components/feedback/Drawer";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { notify } from "../../components/feedback/Toast";
import { notifyError } from "../../utils/notify";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { ModelGroup } from "../../types/gateway";

interface Props {
  groups: ModelGroup[];
  currentUserId: string;
  isAdmin: boolean;
  onClose: () => void;
  onChanged: () => Promise<void>;
}

interface GroupForm {
  name: string;
  description: string;
  enabled: boolean;
}

const emptyForm = (): GroupForm => ({
  name: "",
  description: "",
  enabled: true,
});

export function ModelsGroupsDrawer({
  groups,
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
  const [saving, setSaving] = useState(false);
  const selected = manageable.find((group) => group.id === selectedId) ?? null;

  useEffect(() => {
    if (!selected) {
      setForm(emptyForm());
      return;
    }
    setForm({
      name: selected.name,
      description: selected.description ?? "",
      enabled: selected.is_enabled,
    });
  }, [selected]);

  const choose = (group: ModelGroup | null) => {
    setSelectedId(group?.id ?? null);
  };

  const save = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
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
        setSelectedId(created.id);
      }
      notify(selected ? "模型分组已更新" : "模型分组已创建", "success");
      await onChanged();
    } catch (caught) {
      notifyError(caught, "保存失败");
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
      notifyError(caught, "删除失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      title="管理模型分组"
      description="创建、改名、启停模型分组；分组成员在新建/编辑模型时选择"
      onClose={onClose}
      width="max-w-2xl"
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
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                分组名称<span className="ml-0.5 text-danger">*</span>
              </span>
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
          <div className="rounded-md bg-slate-50 px-3 py-2.5 text-xs text-slate-500">
            分组成员在「新建模型 / 编辑模型」对话框的「所属分组」中维护，一个模型可同时属于多个分组。
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

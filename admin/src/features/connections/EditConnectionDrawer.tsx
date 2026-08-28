import { type FormEvent, useEffect, useState } from "react";
import { Drawer } from "../../components/feedback/Drawer";
import { notify } from "../../components/feedback/Toast";
import { updateConnection } from "../../api/connections";
import type { IMConnection, PlatformDefinition } from "../../types/connections";

interface Props {
  connection: IMConnection;
  platform: PlatformDefinition | null;
  onClose: () => void;
  onSaved: () => void;
}

export function EditConnectionDrawer({ connection, platform, onClose, onSaved }: Props) {
  const [name, setName] = useState(connection.name);
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!platform) return;
    const defaults: Record<string, string> = {};
    platform.fields.forEach((field) => {
      defaults[field.key] = field.default == null ? "" : String(field.default);
    });
    setValues(defaults);
  }, [platform]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    try {
      const updated = await updateConnection(connection.id, {
        name: name.trim() || undefined,
        config: values,
      });
      notify(`配置已保存（v${updated.config_version}），需应用重启后生效`);
      onSaved();
    } catch (error) {
      notify(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={`编辑连接 · ${connection.name}`}
      description="密钥类字段留空表示保留原值；保存后需在列表中「应用重启」该连接。"
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-4 px-6 py-5">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">连接名称</span>
          <input required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} className="field-input" />
        </label>
        {platform?.fields.map((field) => (
          <label className="block" key={field.key}>
            <span className="mb-1.5 block text-xs font-medium text-slate-600">
              {field.label}
              {field.required && <span className="ml-1 text-red-400">*</span>}
              {field.secret && <span className="ml-2 text-[10px] font-normal text-slate-400">留空保留原值</span>}
            </span>
            {field.kind === "json" ? (
              <textarea value={values[field.key] ?? ""} onChange={(event) => setValues({ ...values, [field.key]: event.target.value })} placeholder={field.placeholder || '{"Authorization":"Bearer ..."}'} className="field-input min-h-20 resize-y font-mono" />
            ) : (
              <input
                type={field.secret ? "password" : field.kind === "number" ? "number" : "text"}
                value={values[field.key] ?? ""}
                onChange={(event) => setValues({ ...values, [field.key]: event.target.value })}
                placeholder={field.placeholder}
                className="field-input"
              />
            )}
          </label>
        ))}
        <div className="sticky bottom-0 flex justify-end gap-2 border-t border-slate-100 bg-white pt-4">
          <button type="button" onClick={onClose} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500 hover:bg-slate-50">取消</button>
          <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50">
            {submitting ? "保存中…" : "保存配置"}
          </button>
        </div>
      </form>
    </Drawer>
  );
}

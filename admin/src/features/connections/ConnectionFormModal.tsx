import { useEffect, useState } from "react";
import { applyConnection, createConnection, updateConnection } from "../../api/connections";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { ImConnection, PlatformSpec } from "../../types/gateway";

interface ConnectionFormModalProps {
  platform: PlatformSpec;
  connection: ImConnection | null;
  onClose: () => void;
  onSaved: (connection: ImConnection) => void;
}

export function ConnectionFormModal({
  platform,
  connection,
  onClose,
  onSaved,
}: ConnectionFormModalProps) {
  const [config, setConfig] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const visibleConfig: Record<string, string> = {};
    for (const field of platform.config_schema) {
      if (field.secret) continue;
      const value = connection?.config[field.name];
      if (typeof value === "string" || typeof value === "number") {
        visibleConfig[field.name] = String(value);
      }
    }
    setConfig(visibleConfig);
  }, [connection, platform]);

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      let saved = connection
        ? await updateConnection(connection.id, { config })
        : await createConnection({
            name: platform.label,
            platform: platform.code,
            config,
          });
      if (connection?.desired_running) {
        saved = await applyConnection(connection.id);
      }
      notify(connection ? "配置已保存" : "连接已配置", "success");
      onSaved(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`${platform.label}配置`} onClose={onClose}>
      <div className="space-y-4 p-6">
        {connection && platform.config_schema.some((field) => field.secret) && (
          <p className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
            已保存的 Secret 不会显示，留空表示不修改。
          </p>
        )}

        <div className="space-y-4 rounded-md border border-slate-200 bg-slate-50/60 p-4">
          {platform.config_schema.map((field) => (
            <label key={field.name} className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                {field.label}
                {field.required && <span className="ml-1 text-danger">*</span>}
              </span>
              <input
                type={field.secret ? "password" : field.type === "url" ? "url" : "text"}
                value={config[field.name] ?? ""}
                onChange={(event) =>
                  setConfig((previous) => ({ ...previous, [field.name]: event.target.value }))
                }
                className="field-input"
                placeholder={field.description || field.label}
                autoComplete="new-password"
              />
            </label>
          ))}
        </div>

        {error && (
          <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={saving}>
            <Icon name="check" className="h-4 w-4" />
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}

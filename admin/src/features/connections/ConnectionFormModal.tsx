import { useEffect, useMemo, useState } from "react";
import { createConnection, updateConnection } from "../../api/connections";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { ImConnection, PlatformSpec } from "../../types/gateway";

interface ConnectionFormModalProps {
  platforms: PlatformSpec[];
  connection?: ImConnection;
  onClose: () => void;
  onSaved: (connection: ImConnection) => void;
}

export function ConnectionFormModal({
  platforms,
  connection,
  onClose,
  onSaved,
}: ConnectionFormModalProps) {
  const [name, setName] = useState(connection?.name ?? "");
  const [platform, setPlatform] = useState(connection?.platform ?? platforms[0]?.code ?? "");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const spec = useMemo(
    () => platforms.find((item) => item.code === platform),
    [platforms, platform],
  );

  useEffect(() => {
    // 编辑时 Secret 不回显：留空表示保留原值。
    setConfig({});
  }, [platform]);

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      // 编辑接口不接受 platform（平台不可变更），否则后端严格校验返回 422。
      const saved = connection
        ? await updateConnection(connection.id, { name: name.trim(), config })
        : await createConnection({ name: name.trim(), platform, config });
      notify(connection ? "连接已更新" : "连接已创建");
      onSaved(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={connection ? "编辑连接" : "创建连接"}
      description={
        connection ? "Secret 留空保留原值，填了才替换。" : "Secret 保存后不再显示。"
      }
      onClose={onClose}
    >
      <div className="space-y-4 p-6">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">连接名称</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="field-input"
            placeholder="例如：企微值班号"
            maxLength={100}
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">平台</span>
          <select
            value={platform}
            onChange={(event) => setPlatform(event.target.value)}
            className="field-input"
            disabled={Boolean(connection)}
          >
            {platforms.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        {spec && (
          <div className="space-y-4 rounded-md border border-slate-200 bg-slate-50/60 p-4">
            <p className="text-xs leading-5 text-slate-500">{spec.description}</p>
            {spec.config_schema.map((field) => (
              <label key={field.name} className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  {field.label}
                  {field.required && <span className="ml-1 text-danger">*</span>}
                  {field.secret && <span className="ml-2 text-slate-400">（保存后不显示）</span>}
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
        )}

        {error && (
          <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={saving} disabled={!name.trim()}>
            <Icon name="check" className="h-4 w-4" />
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}

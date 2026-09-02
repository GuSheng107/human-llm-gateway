import { useCallback, useEffect, useMemo, useState } from "react";
import { applyConnection, createConnection, updateConnection } from "../../api/connections";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { ImConnection, PlatformSpec } from "../../types/gateway";
import { ConnectionSetupSection } from "./ConnectionSetupModal";
import { QrLoginSection } from "./QrLoginDrawer";

interface ConnectionFormModalProps {
  platform: PlatformSpec;
  connection: ImConnection | null;
  loadingConnection?: boolean;
  readOnly?: boolean;
  onClose: () => void;
  onSaved: (connection: ImConnection) => void;
}

function withoutGeneratedTokens(connection: ImConnection): ImConnection {
  return { ...connection, generated_tokens: null };
}

export function ConnectionFormModal({
  platform,
  connection,
  loadingConnection = false,
  readOnly = false,
  onClose,
  onSaved,
}: ConnectionFormModalProps) {
  const [current, setCurrent] = useState<ImConnection | null>(connection);
  const [config, setConfig] = useState<Record<string, string>>({});
  const [generatedTokens, setGeneratedTokens] = useState<Record<string, string>>(
    connection?.generated_tokens ?? {},
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const editableFields = useMemo(
    () => platform.config_schema.filter((field) => field.credential_kind !== "gateway_token"),
    [platform.config_schema],
  );
  const gatewayFields = useMemo(
    () => platform.config_schema.filter((field) => field.credential_kind === "gateway_token"),
    [platform.config_schema],
  );

  useEffect(() => {
    const visibleConfig: Record<string, string> = {};
    for (const field of editableFields) {
      if (field.secret) continue;
      const value = connection?.config[field.name];
      if (typeof value === "string" || typeof value === "number") {
        visibleConfig[field.name] = String(value);
      }
    }
    setCurrent(connection);
    setConfig(visibleConfig);
    setGeneratedTokens(connection?.generated_tokens ?? {});
  }, [connection, editableFields]);

  const publishConnection = useCallback(
    (saved: ImConnection) => {
      const safeConnection = withoutGeneratedTokens(saved);
      setCurrent(safeConnection);
      onSaved(safeConnection);
    },
    [onSaved],
  );

  const submit = async () => {
    if (readOnly) return;
    setSaving(true);
    setError("");
    try {
      let saved = current
        ? await updateConnection(current.id, { config })
        : await createConnection({
            name: platform.label,
            platform: platform.code,
            config,
          });
      if (saved.generated_tokens) setGeneratedTokens(saved.generated_tokens);
      if (current?.desired_running) saved = await applyConnection(current.id);
      publishConnection(saved);
      notify(current ? "配置已保存" : "连接已创建并保存", "success");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const connectionChanged = useCallback(
    (saved: ImConnection) => publishConnection(saved),
    [publishConnection],
  );

  const tokenGenerated = useCallback((field: string, token: string) => {
    setGeneratedTokens((previous) => ({ ...previous, [field]: token }));
  }, []);

  return (
    <Modal
      title={`${platform.label}配置与接入`}
      onClose={onClose}
      width="max-w-4xl"
    >
      <div className="max-h-[78vh] space-y-5 overflow-y-auto p-5 sm:p-6">
        {readOnly && (
          <div
            role="status"
            className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
          >
            管理员只读视图 · 此弹窗仅展示配置与连接信息，不会调用保存接口。
          </div>
        )}
        {!platform.supports_login && !readOnly && (
          <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-700">连接配置</h3>
                <p className="mt-1 text-xs text-slate-500">
                  {current ? "修改后保存。" : "保存后按指引接入。"}
                </p>
              </div>
              {current && <span className="text-xs font-medium text-emerald-600">已保存</span>}
            </div>

            {current && editableFields.some((field) => field.secret) && (
              <p className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                已保存的 Secret 已隐藏，留空不变。
              </p>
            )}

            <div className="mt-4 space-y-4">
              {editableFields.map((field) => (
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

              {editableFields.length === 0 && (
                <p className="rounded-lg border border-dashed border-slate-300 bg-white px-3 py-3 text-xs text-slate-500">
                  无需填写配置。{gatewayFields.length > 0 ? "保存后生成接入 Token。" : ""}
                </p>
              )}
            </div>

            <div className="mt-4 flex justify-end">
              <Button onClick={() => void submit()} loading={saving}>
                <Icon name="check" className="h-4 w-4" />
                {current ? "保存配置" : "保存并生成接入信息"}
              </Button>
            </div>
          </section>
        )}

        {loadingConnection ? (
          <section
            className="grid min-h-44 place-items-center rounded-xl border border-slate-200 bg-slate-50/60"
            aria-live="polite"
          >
            <div className="text-center">
              <span className="mx-auto block h-6 w-6 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
              <p className="mt-3 text-xs text-slate-500">正在准备连接…</p>
            </div>
          </section>
        ) : current ? (
          platform.supports_login ? (
            <QrLoginSection
              connection={current}
              onBound={() => connectionChanged({ ...current, bound: true, state: "stopped" })}
              disabled={readOnly}
            />
          ) : (
            <ConnectionSetupSection
              connection={current}
              platform={platform}
              generatedTokens={generatedTokens}
              onConnectionChange={connectionChanged}
              onTokenGenerated={tokenGenerated}
              disabled={readOnly}
            />
          )
        ) : (
          !readOnly && (
            <section className="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center">
              <Icon name="info-circle" className="mx-auto h-5 w-5 text-slate-300" />
              <p className="mt-2 text-xs text-slate-500">保存后显示 URL、Token 和 curl 命令。</p>
            </section>
          )
        )}

        {error && (
          <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
            {error}
          </p>
        )}

        <div className="flex justify-end border-t border-slate-100 pt-4">
          <Button variant="ghost" onClick={onClose}>完成</Button>
        </div>
      </div>
    </Modal>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bindingStatus,
  cancelBinding,
  createBinding,
  rotateCredential,
} from "../../api/connections";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { notify } from "../../components/feedback/Toast";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { BindingStatus, ImConnection, PlatformSpec } from "../../types/gateway";
import { copyText } from "../../utils/clipboard";
import { platformSetupGuide } from "./connectionPresentation";
import { friendlyErrorMessage } from "../../utils/notify";

interface ConnectionSetupSectionProps {
  connection: ImConnection;
  platform: PlatformSpec;
  generatedTokens: Record<string, string>;
  onConnectionChange: (connection: ImConnection) => void;
  onTokenGenerated: (field: string, token: string) => void;
  disabled?: boolean;
}

export function ConnectionSetupSection({
  connection,
  platform,
  generatedTokens,
  onConnectionChange,
  onTokenGenerated,
  disabled = false,
}: ConnectionSetupSectionProps) {
  const [binding, setBinding] = useState<BindingStatus | null>(null);
  const [command, setCommand] = useState(platform.binding_command ?? "");
  const [bindingBusy, setBindingBusy] = useState(false);
  const [rotatingField, setRotatingField] = useState<string | null>(null);
  const [error, setError] = useState("");
  const bindingStartedRef = useRef<string | null>(null);
  const guide = useMemo(
    () => platformSetupGuide(connection, window.location.origin, generatedTokens),
    [connection, generatedTokens],
  );
  const gatewayFields = platform.config_schema.filter(
    (field) => field.credential_kind === "gateway_token",
  );
  const bound = binding?.bound ?? connection.bound;
  const bindingExpired = Boolean(binding && !binding.bound && !binding.binding_pending);

  const beginBinding = useCallback(async () => {
    if (!platform.binding_command || connection.bound || disabled) return;
    setBindingBusy(true);
    setError("");
    try {
      const created = await createBinding(connection.id);
      setCommand(created.binding_code);
      setBinding(await bindingStatus(connection.id));
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "发起绑定失败"));
    } finally {
      setBindingBusy(false);
    }
  }, [connection.bound, connection.id, platform.binding_command, disabled]);

  useEffect(() => {
    if (!platform.binding_command) return;
    if (disabled) {
      setBinding({
        bound: connection.bound,
        binding_pending: false,
        binding_expires_at: null,
      });
      return;
    }
    if (connection.bound) {
      setBinding({ bound: true, binding_pending: false, binding_expires_at: null });
      return;
    }
    if (bindingStartedRef.current === connection.id) return;
    bindingStartedRef.current = connection.id;
    void beginBinding();
  }, [beginBinding, connection.bound, connection.id, platform.binding_command, disabled]);

  useEffect(() => {
    if (!binding?.binding_pending || binding.bound) return;
    const timer = window.setInterval(() => {
      void bindingStatus(connection.id)
        .then((status) => {
          setBinding(status);
          if (status.bound) {
            notify("IM 连接绑定成功，现在可以开启连接", "success");
            onConnectionChange({ ...connection, bound: true, state: "stopped" });
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [binding?.binding_pending, binding?.bound, connection, onConnectionChange]);

  const stopBinding = async () => {
    setBindingBusy(true);
    setError("");
    try {
      const saved = await cancelBinding(connection.id);
      setBinding({ bound: saved.bound, binding_pending: false, binding_expires_at: null });
      notify("本次绑定已取消，连接与配置已保留", "info");
      onConnectionChange(saved);
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "取消绑定失败"));
    } finally {
      setBindingBusy(false);
    }
  };

  const rotateToken = async (field: string, label: string) => {
    const confirmed = await confirmAction({
      message: `重新生成${label}后，旧 Token 会立即失效。确认继续？`,
    });
    if (!confirmed) return;
    setRotatingField(field);
    setError("");
    try {
      const result = await rotateCredential(connection.id, field);
      onTokenGenerated(result.field, result.token);
      notify(`${label}已重新生成，请立即复制并更新客户端`, "success");
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "重新生成失败"));
    } finally {
      setRotatingField(null);
    }
  };

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-700">接入指引</h3>
          {platform.binding_command ? (
            <StatusBadge status={bound ? "bound" : bindingExpired ? "expired" : "waiting"} />
          ) : platform.requires_binding ? (
            <StatusBadge status={bound ? "bound" : "waiting"} />
          ) : (
            <span className="text-xs font-medium text-slate-400">无需消息绑定</span>
          )}
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-500">{guide.description}</p>
      </section>

      {gatewayFields.map((field) => {
        const token = generatedTokens[field.name];
        const configured = Boolean(connection.config[`${field.name}_set`]);
        return (
          <section key={field.name} className="rounded-xl border border-amber-200 bg-amber-50/50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold text-amber-900">{field.label}</h3>
                <p className="mt-1 text-caption text-amber-700/80">
                  {token
                    ? "明文仅本次展示，关闭窗口后无法再次查看。"
                    : configured
                      ? "已配置，明文不可再次查看。"
                      : "保存后由系统自动生成。"}
                </p>
              </div>
              <Button
                variant="ghost"
                loading={rotatingField === field.name}
                disabled={disabled}
                onClick={() => void rotateToken(field.name, field.label)}
              >
                重新生成
              </Button>
            </div>
            {token && (
              <div className="mt-3 flex flex-col gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                <code className="break-all text-xs font-semibold text-slate-700">{token}</code>
                <Button variant="ghost" onClick={() => void copyText(token, field.label)}>
                  <Icon name="copy" className="h-4 w-4" />复制
                </Button>
              </div>
            )}
          </section>
        );
      })}

      {guide.endpoints.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-xs font-semibold text-slate-700">完整 URL 地址</h3>
          <div className="mt-3 space-y-2">
            {guide.endpoints.map((endpoint) => (
              <div
                key={endpoint.label}
                className="flex flex-col gap-2 rounded-lg bg-slate-50 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="text-micro text-slate-400">{endpoint.label}</p>
                  <code className="mt-0.5 block break-all text-xs font-semibold text-slate-700">
                    {endpoint.value}
                  </code>
                </div>
                <Button
                  variant="ghost"
                  onClick={() => void copyText(endpoint.value, endpoint.label)}
                >
                  <Icon name="copy" className="h-4 w-4" />复制
                </Button>
              </div>
            ))}
          </div>
        </section>
      )}

      {guide.commands.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="text-xs font-semibold text-slate-700">curl 命令</h3>
          <div className="mt-3 space-y-3">
            {guide.commands.map((item) => (
              <div key={item.label}>
                <p className="mb-1.5 text-micro font-medium text-slate-400">{item.label}</p>
                <div className="relative rounded-lg bg-slate-900 p-3 pr-14">
                  <pre className="overflow-x-auto whitespace-pre-wrap break-all text-xs leading-5 text-slate-100">
                    {item.value}
                  </pre>
                  <button
                    type="button"
                    aria-label={`复制${item.label}`}
                    onClick={() => void copyText(item.value, item.label)}
                    className="absolute right-2 top-2 rounded-md p-2 text-slate-300 hover:bg-white/10 hover:text-white"
                  >
                    <Icon name="copy" className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {guide.commandLabel && command && (
        <section className="rounded-xl border border-blue-100 bg-blue-50/70 p-4">
          <p className="text-xs font-medium text-blue-700">{guide.commandLabel}</p>
          <div className="mt-3 flex flex-col gap-3 rounded-lg border border-blue-100 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <code className="break-all text-base font-semibold tracking-wide text-blue-800">
              {command}
            </code>
            <Button onClick={() => void copyText(command, "绑定命令")}>
              <Icon name="copy" className="h-4 w-4" />复制命令
            </Button>
          </div>
          {guide.commandHelp && (
            <p className="mt-3 text-caption leading-5 text-blue-600/80">{guide.commandHelp}</p>
          )}
          <div className="mt-3 flex justify-end gap-2">
            {error && binding === null && !disabled && (
              <Button variant="ghost" loading={bindingBusy} onClick={() => void beginBinding()}>
                重新发起绑定
              </Button>
            )}
            {bindingExpired && !disabled && (
              <Button variant="ghost" loading={bindingBusy} onClick={() => void beginBinding()}>
                重新发起绑定
              </Button>
            )}
            {binding?.binding_pending && !disabled && (
              <Button variant="ghost" loading={bindingBusy} onClick={() => void stopBinding()}>
                {platform.kind === "client" ? "取消本次绑定监听" : "取消本次绑定"}
              </Button>
            )}
          </div>
        </section>
      )}

      {error && (
        <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}

import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { BindingStatus, ImConnection, PlatformSpec } from "../../types/gateway";
import { copyText } from "../../utils/clipboard";
import { platformSetupGuide } from "./connectionPresentation";

interface ConnectionSetupModalProps {
  connection: ImConnection;
  platform: PlatformSpec;
  command: string;
  binding: BindingStatus | null;
  onClose: () => void;
}

export function ConnectionSetupModal({
  connection,
  platform,
  command,
  binding,
  onClose,
}: ConnectionSetupModalProps) {
  const guide = platformSetupGuide(connection);
  const bound = binding?.bound ?? connection.bound;
  const bindingExpired = Boolean(binding && !binding.bound && !binding.binding_pending);

  return (
    <Modal
      title={guide.title}
      description={guide.description}
      onClose={onClose}
      width="max-w-2xl"
    >
      <div className="max-h-[72vh] space-y-4 overflow-y-auto p-6">
        <div className="grid gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4 sm:grid-cols-3">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate-400">连接</p>
            <p className="mt-1 truncate text-sm font-semibold text-slate-700">{connection.name}</p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate-400">正式运行</p>
            <div className="mt-1"><StatusBadge status={connection.desired_running ? "active" : "inactive"} /></div>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate-400">绑定状态</p>
            <div className="mt-1">
              {platform.binding_command || platform.supports_login ? (
                <StatusBadge status={bound ? "bound" : bindingExpired ? "expired" : "waiting"} />
              ) : (
                <span className="text-xs font-medium text-slate-500">无需绑定</span>
              )}
            </div>
          </div>
        </div>

        {guide.endpoints.length > 0 && (
          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="text-xs font-semibold text-slate-700">接入地址</h3>
            <div className="mt-3 space-y-2">
              {guide.endpoints.map((endpoint) => (
                <div
                  key={endpoint.label}
                  className="flex flex-col gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="text-micro text-slate-400">{endpoint.label}</p>
                    <code className="mt-0.5 block break-all text-xs font-semibold text-slate-700">
                      {endpoint.value}
                    </code>
                  </div>
                  <button
                    type="button"
                    onClick={() => void copyText(endpoint.value, endpoint.label)}
                    className="inline-flex shrink-0 items-center gap-1.5 self-end rounded-md px-2 py-1.5 text-xs font-medium text-primary transition hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 sm:self-auto"
                  >
                    <Icon name="copy" className="h-3.5 w-3.5" />
                    复制
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        {guide.commandLabel && command && (
          <section className="rounded-xl border border-blue-100 bg-blue-50/70 p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-blue-700">{guide.commandLabel}</p>
              {bound && (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                  <Icon name="check-circle" className="h-3.5 w-3.5" />
                  已完成
                </span>
              )}
            </div>
            <div className="mt-3 flex flex-col gap-3 rounded-lg border border-blue-100 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <code className="break-all text-base font-semibold tracking-wide text-blue-800">
                {command}
              </code>
              <Button onClick={() => void copyText(command, "绑定命令")}>
                <Icon name="copy" className="h-4 w-4" />
                复制命令
              </Button>
            </div>
            {guide.commandHelp && <p className="mt-3 text-caption leading-5 text-blue-600/80">{guide.commandHelp}</p>}
          </section>
        )}

        <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-xs leading-5 text-slate-500">
          <Icon name="info-circle" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <p>
            需要绑定的平台，绑定成功后才能启用。关闭窗口会停止企微绑定会话。
          </p>
        </div>

        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>完成</Button>
        </div>
      </div>
    </Modal>
  );
}

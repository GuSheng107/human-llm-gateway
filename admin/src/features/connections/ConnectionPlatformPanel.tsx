import { StatusBadge } from "../../components/data-display/StatusBadge";
import { Icon } from "../../icons";
import type { ImConnection, PlatformSpec } from "../../types/gateway";
import { platformVisual } from "./connectionPresentation";

interface ConnectionPlatformPanelProps {
  platform: PlatformSpec;
  connection: ImConnection | null;
  busy: boolean;
  onToggle: () => void;
  onPrimaryAction: () => void;
  onDelete: () => void;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "尚未检查";
}

function RunningSwitch({
  platform,
  connection,
  busy,
  onToggle,
}: {
  platform: PlatformSpec;
  connection: ImConnection | null;
  busy: boolean;
  onToggle: () => void;
}) {
  const running = Boolean(connection?.desired_running);
  const disabled = !connection || busy;
  return (
    <label className="inline-flex shrink-0 items-center gap-2 text-xs text-slate-500">
      <button
        type="button"
        role="switch"
        aria-checked={running}
        aria-label={`${platform.label}启用`}
        disabled={disabled}
        onClick={onToggle}
        className={`relative h-6 w-11 rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-45 ${
          running ? "bg-primary" : "bg-slate-300"
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition ${
            running ? "left-[22px]" : "left-0.5"
          }`}
        />
      </button>
      <span className={running ? "font-medium text-primary" : "text-slate-400"}>
        {running ? "已启用" : "未启用"}
      </span>
    </label>
  );
}

function ActionButton({
  children,
  danger = false,
  disabled = false,
  onClick,
}: {
  children: string;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md px-2.5 py-2 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 disabled:cursor-wait disabled:opacity-50 ${
        danger
          ? "text-red-500 hover:bg-red-50 focus-visible:ring-red-200"
          : "text-primary hover:bg-blue-50 focus-visible:ring-primary/20"
      }`}
    >
      {children}
    </button>
  );
}

export function ConnectionPlatformPanel({
  platform,
  connection,
  busy,
  onToggle,
  onPrimaryAction,
  onDelete,
}: ConnectionPlatformPanelProps) {
  const visual = platformVisual(platform.code);
  const isWechat = platform.supports_login;
  const abnormal = connection?.state === "error" || connection?.state === "auth_required";

  return (
    <section className="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-card">
      <span className={`absolute inset-y-0 left-0 w-1 ${visual.railClass}`} />
      <div
        className={`grid gap-4 bg-gradient-to-r ${visual.headerClass} px-5 py-5 pl-6 lg:grid-cols-[minmax(0,1fr)_minmax(15rem,.8fr)_auto] lg:items-center`}
      >
        <div className="flex min-w-0 items-center gap-3.5">
          <span
            className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl border text-xs font-bold tracking-tight ${visual.iconClass}`}
          >
            {visual.mark}
          </span>
          <div className="min-w-0">
            <p className="text-micro font-semibold tracking-[0.16em] text-slate-400">
              {visual.eyebrow}
            </p>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-slate-800">{platform.label}</h2>
              {connection ? (
                <StatusBadge status={connection.state} fallback={connection.state} />
              ) : (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-micro text-slate-500">
                  未配置
                </span>
              )}
              {platform.requires_binding && connection && (
                <StatusBadge status={connection.bound ? "bound" : "unbound"} />
              )}
            </div>
          </div>
        </div>

        <div className="min-w-0 rounded-lg border border-white/80 bg-white/70 px-3 py-2.5 text-xs shadow-sm">
          {connection ? (
            abnormal ? (
              <div className="flex items-start gap-2 text-red-600">
                <Icon name="warning" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>连接异常，请重新{isWechat ? "扫码" : "配置"}</span>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-slate-500">
                <span>{connection.desired_running ? "连接已开启" : "连接未开启"}</span>
                <span className="text-slate-400">
                  最近检查：{formatTime(connection.last_health_at)}
                </span>
              </div>
            )
          ) : (
            <div className="flex items-center gap-2 text-slate-500">
              <Icon name="info-circle" className="h-3.5 w-3.5 shrink-0" />
              <span>{isWechat ? "扫码后完成绑定" : "完成配置后即可使用"}</span>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-1 border-t border-slate-200/70 pt-3 lg:border-0 lg:pt-0">
          <RunningSwitch
            platform={platform}
            connection={connection}
            busy={busy}
            onToggle={onToggle}
          />
          <ActionButton disabled={busy} onClick={onPrimaryAction}>
            {isWechat ? "绑定（扫码）" : "配置"}
          </ActionButton>
          {connection && (
            <ActionButton danger disabled={busy} onClick={onDelete}>
              删除
            </ActionButton>
          )}
        </div>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bindingStatus,
  checkConnections,
  createBinding,
  createConnection,
  deleteConnection,
  listAllConnections,
  listPlatforms,
  startConnection,
  stopConnection,
} from "../../api/connections";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type {
  BindingStatus as BindingStatusType,
  ConnectionCheckItem,
  ImConnection,
  PlatformSpec,
} from "../../types/gateway";
import { ConnectionFormModal } from "./ConnectionFormModal";
import { ConnectionPlatformPanel } from "./ConnectionPlatformPanel";
import { ConnectionSetupModal } from "./ConnectionSetupModal";
import { orderPlatforms } from "./connectionPresentation";
import { QrLoginDrawer } from "./QrLoginDrawer";

interface ConfigTarget {
  platform: PlatformSpec;
  connection: ImConnection | null;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "-";
}

export function ConnectionsPage() {
  const [items, setItems] = useState<ImConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [configTarget, setConfigTarget] = useState<ConfigTarget | null>(null);
  const [qrConnection, setQrConnection] = useState<ImConnection | null>(null);
  const [healthReport, setHealthReport] = useState<ConnectionCheckItem[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [setupConnection, setSetupConnection] = useState<ImConnection | null>(null);
  const [binding, setBinding] = useState<BindingStatusType | null>(null);
  const [bindingCommand, setBindingCommand] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const displayPlatforms = useMemo(
    () => orderPlatforms(platforms, items),
    [items, platforms],
  );
  const platformMap = useMemo(
    () => new Map(displayPlatforms.map((platform) => [platform.code, platform])),
    [displayPlatforms],
  );
  const connectionMap = useMemo(() => {
    const byPlatform = new Map<string, ImConnection>();
    for (const item of items) byPlatform.set(item.platform, item);
    return byPlatform;
  }, [items]);
  const setupPlatform = setupConnection
    ? platformMap.get(setupConnection.platform) ?? null
    : null;
  const summary = useMemo(
    () => ({
      total: items.length,
      enabled: items.filter((item) => item.desired_running).length,
      bound: items.filter((item) => item.bound).length,
      abnormal: items.filter(
        (item) => item.state === "error" || item.state === "auth_required",
      ).length,
    }),
    [items],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [connections, platformList] = await Promise.all([
        listAllConnections(),
        listPlatforms(),
      ]);
      setItems(connections);
      setPlatforms(platformList);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  useEffect(() => {
    if (
      !setupConnection ||
      !bindingCommand ||
      binding?.bound ||
      (binding !== null && !binding.binding_pending)
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void bindingStatus(setupConnection.id)
        .then((status) => {
          setBinding(status);
          if (status.bound) {
            notify("IM 连接绑定成功，现在可以开启连接", "success");
            setSetupConnection((current) =>
              current ? { ...current, bound: true, state: "stopped" } : current,
            );
            void load();
          } else if (!status.binding_pending) {
            notify("绑定窗口已结束，请重新打开配置", "info");
            void stopConnection(setupConnection.id).then(load).catch(() => undefined);
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [binding, bindingCommand, load, setupConnection]);

  const openSetup = async (item: ImConnection) => {
    const spec = platformMap.get(item.platform);
    if (!spec) {
      notify("平台信息不可用，请刷新后重试", "error");
      return;
    }
    if (spec.supports_login) {
      setQrConnection(item);
      return;
    }

    setBusyKey(item.id);
    try {
      let command = spec.binding_command ?? "";
      let currentBinding: BindingStatusType | null = null;
      if (spec.binding_command) {
        if (!item.bound) {
          const created = await createBinding(item.id);
          command = created.binding_code;
        }
        currentBinding = await bindingStatus(item.id);
      }
      setSetupConnection(item);
      setBindingCommand(command);
      setBinding(currentBinding);
      if (item.platform === "wecom_aibot" && !item.bound) {
        notify("企微绑定已启动，请在个人会话发送固定命令", "success");
      }
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "打开配置失败", "error");
    } finally {
      setBusyKey(null);
    }
  };

  const scanWechat = async (platform: PlatformSpec, current: ImConnection | null) => {
    const key = current?.id ?? platform.code;
    setBusyKey(key);
    try {
      let connection = current;
      if (!connection) {
        connection = await createConnection({
          name: platform.label,
          platform: platform.code,
          config: {},
        });
        setItems((previous) => [...previous, connection as ImConnection]);
      }
      setQrConnection(connection);
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "发起扫码失败", "error");
      await load();
    } finally {
      setBusyKey(null);
    }
  };

  const toggle = async (platform: PlatformSpec, item: ImConnection | null) => {
    if (!item) return;
    if (!item.desired_running && platform.requires_binding && !item.bound) {
      notify(
        platform.supports_login
          ? "请先完成微信扫码绑定"
          : "请先完成企微用户绑定",
        "info",
      );
      if (platform.supports_login) void scanWechat(platform, item);
      else void openSetup(item);
      return;
    }

    setBusyKey(item.id);
    try {
      if (item.desired_running) await stopConnection(item.id);
      else await startConnection(item.id);
      notify(item.desired_running ? "连接已关闭" : "连接已开启", "success");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败", "error");
    } finally {
      setBusyKey(null);
    }
  };

  const remove = async (item: ImConnection) => {
    if (!(await confirmAction({ message: `确认删除「${item.platform_label}」连接？` }))) {
      return;
    }
    setBusyKey(item.id);
    try {
      await deleteConnection(item.id);
      notify("连接已删除", "success");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败", "error");
    } finally {
      setBusyKey(null);
    }
  };

  const inspectConnections = async () => {
    setChecking(true);
    try {
      const report = await checkConnections();
      setHealthReport(report);
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "检查失败", "error");
    } finally {
      setChecking(false);
    }
  };

  const closeSetup = () => {
    const shouldStopBindingSession = Boolean(
      setupConnection?.platform === "wecom_aibot" &&
        !setupConnection.bound &&
        !setupConnection.desired_running,
    );
    const connectionId = setupConnection?.id;
    setSetupConnection(null);
    setBindingCommand("");
    setBinding(null);
    if (shouldStopBindingSession && connectionId) {
      void stopConnection(connectionId).then(load).catch(() => undefined);
    }
  };

  const saveConnection = (saved: ImConnection) => {
    setConfigTarget(null);
    void load();
    void openSetup(saved);
  };

  return (
    <div className="flex h-[calc(100dvh-6rem)] min-h-0 flex-col gap-4 overflow-hidden sm:h-[calc(100dvh-7rem)] lg:h-[calc(100dvh-7.5rem)]">
      <PageHeader
        title="连接 IM"
        actions={
          <Button variant="ghost" loading={checking} onClick={() => void inspectConnections()}>
            <Icon name="refresh" className="h-4 w-4" />
            检查连接
          </Button>
        }
      />

      <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary-soft text-primary">
              <Icon name="link" className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-slate-700">平台连接总览</h2>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-slate-400">
            <span>连接 <strong className="text-slate-700">{summary.total}</strong></span>
            <span>已启用 <strong className="text-primary">{summary.enabled}</strong></span>
            <span>已绑定 <strong className="text-emerald-600">{summary.bound}</strong></span>
            <span>
              异常{" "}
              <strong className={summary.abnormal ? "text-red-500" : "text-slate-600"}>
                {summary.abnormal}
              </strong>
            </span>
            {loading && (
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-200 border-t-primary"
                aria-label="正在刷新"
              />
            )}
          </div>
        </div>

        {error && <ErrorBanner message={error} className="mx-4 mt-4 sm:mx-5" />}

        <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50/55 p-3 sm:p-4">
          <div className="space-y-3 pb-1">
            {displayPlatforms.map((platform) => {
              const connection = connectionMap.get(platform.code) ?? null;
              const key = connection?.id ?? platform.code;
              return (
                <ConnectionPlatformPanel
                  key={platform.code}
                  platform={platform}
                  connection={connection}
                  busy={busyKey === key}
                  onToggle={() => void toggle(platform, connection)}
                  onPrimaryAction={() => {
                    if (platform.supports_login) void scanWechat(platform, connection);
                    else setConfigTarget({ platform, connection });
                  }}
                  onDelete={() => connection && void remove(connection)}
                />
              );
            })}

            {!loading && displayPlatforms.length === 0 && (
              <div className="grid min-h-56 place-items-center rounded-xl border border-dashed border-slate-200 bg-white text-center">
                <div>
                  <Icon name="link" className="mx-auto h-7 w-7 text-slate-300" />
                  <p className="mt-2 text-xs text-slate-400">暂无可用 IM 平台</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {configTarget && (
        <ConnectionFormModal
          platform={configTarget.platform}
          connection={configTarget.connection}
          onClose={() => setConfigTarget(null)}
          onSaved={saveConnection}
        />
      )}

      {healthReport && (
        <Modal
          title="IM 连接检查结果"
          description={`共检查 ${healthReport.length} 个连接；异常且已启用的连接已自动关闭开关。`}
          onClose={() => setHealthReport(null)}
          width="max-w-4xl"
        >
          <div className="max-h-[70vh] space-y-3 overflow-y-auto p-5">
            {healthReport.map((item) => (
              <section
                key={item.id}
                className={`rounded-lg border p-4 ${
                  item.abnormal ? "border-red-200 bg-red-50/50" : "border-slate-200 bg-white"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-slate-700">
                      {item.platform_label}
                    </h3>
                    <StatusBadge status={item.state} fallback={item.state} />
                    <StatusBadge status={item.bound ? "bound" : "unbound"} />
                  </div>
                  <span
                    className={`text-xs font-medium ${
                      item.abnormal ? "text-red-600" : "text-emerald-600"
                    }`}
                  >
                    {item.auto_disabled
                      ? "异常，已自动关闭"
                      : item.abnormal
                        ? "异常"
                        : "检查正常"}
                  </span>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 pt-3 text-xs md:grid-cols-4">
                  <div>
                    <dt className="text-slate-400">启用开关</dt>
                    <dd className="mt-0.5 text-slate-600">
                      {item.desired_running ? "开启" : "关闭"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">运行实例</dt>
                    <dd className="mt-0.5 text-slate-600">
                      {item.runtime.running ? "在线" : "未运行"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">重试次数</dt>
                    <dd className="mt-0.5 text-slate-600">{item.retry_count}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-400">最近检查</dt>
                    <dd className="mt-0.5 text-slate-600">
                      {formatTime(item.last_health_at)}
                    </dd>
                  </div>
                </dl>
              </section>
            ))}
            {healthReport.length === 0 && (
              <p className="py-12 text-center text-xs text-slate-400">暂无可检查的连接</p>
            )}
          </div>
        </Modal>
      )}

      {setupConnection && setupPlatform && (
        <ConnectionSetupModal
          connection={setupConnection}
          platform={setupPlatform}
          command={bindingCommand}
          binding={binding}
          onClose={closeSetup}
        />
      )}

      <QrLoginDrawer
        connection={qrConnection}
        onClose={() => setQrConnection(null)}
        onSaved={() => {
          notify("扫码绑定成功，现在可以开启连接", "success");
          void load();
        }}
      />
    </div>
  );
}

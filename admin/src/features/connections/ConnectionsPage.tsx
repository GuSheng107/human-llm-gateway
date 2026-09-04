import { useCallback, useEffect, useMemo, useState } from "react";
import {
  checkConnections,
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
  ConnectionCheckItem,
  ImConnection,
  PlatformSpec,
} from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";
import { ConnectionFormModal } from "./ConnectionFormModal";
import { ConnectionPlatformPanel } from "./ConnectionPlatformPanel";
import { orderPlatforms } from "./connectionPresentation";

interface ConfigTarget {
  platform: PlatformSpec;
  connection: ImConnection | null;
  loading: boolean;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "-";
}

function ConnectionListSkeleton() {
  return (
    <div
      className="space-y-3"
      role="status"
      aria-live="polite"
      aria-label="正在加载连接"
    >
      {[0, 1, 2, 3].map((index) => (
        <section
          key={index}
          className="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
        >
          <span className="absolute inset-y-0 left-0 w-1 animate-pulse bg-slate-200" />
          <div className="grid gap-4 bg-gradient-to-r from-slate-50 to-slate-100/70 px-5 py-5 pl-6 lg:grid-cols-[minmax(0,1fr)_minmax(15rem,.8fr)_auto] lg:items-center">
            <div className="flex min-w-0 items-center gap-3.5">
              <span className="grid h-12 w-12 shrink-0 animate-pulse place-items-center rounded-xl bg-slate-200" />
              <div className="min-w-0 flex-1">
                <span className="block h-3 w-16 animate-pulse rounded bg-slate-200" />
                <span className="mt-2 block h-4 w-40 animate-pulse rounded bg-slate-200" />
             </div>
           </div>
            <div className="min-w-0 rounded-lg border border-white/80 bg-white/70 px-3 py-2.5 shadow-sm">
              <span className="block h-3 w-32 animate-pulse rounded bg-slate-200" />
              <span className="mt-2 block h-3 w-24 animate-pulse rounded bg-slate-200" />
           </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200/70 pt-3 lg:border-0 lg:pt-0">
              <span className="h-6 w-11 animate-pulse rounded-full bg-slate-200" />
              <span className="h-4 w-12 animate-pulse rounded bg-slate-200" />
              <span className="h-4 w-8 animate-pulse rounded bg-slate-200" />
           </div>
         </div>
       </section>
      ))}
   </div>
  );
}

export function ConnectionsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState<ImConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [configTarget, setConfigTarget] = useState<ConfigTarget | null>(null);
  const [healthReport, setHealthReport] = useState<ConnectionCheckItem[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  // /api/im-connections 后端已限定为当前用户自己，而管理员无权限创建连接/扫码。
  const showAdminPanel = isAdmin;
  const displayPlatforms = useMemo(
    () => orderPlatforms(platforms, items),
    [items, platforms],
  );
  const connectionMap = useMemo(() => {
    const byPlatform = new Map<string, ImConnection>();
    for (const item of items) byPlatform.set(item.platform, item);
    return byPlatform;
  }, [items]);
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

  const openConnection = async (platform: PlatformSpec, current: ImConnection | null) => {
    // 管理员不可创建或编辑连接；直接走监管页
    if (isAdmin) {
      notify("管理员账号不支持创建或配置 IM 连接，请前往「IM 连接监管」管理已有连接", "info");
      return;
    }
    const key = current?.id ?? platform.code;
    const needsConnection = !current && platform.supports_login;
    // 先展示弹窗，再处理扫码连接的创建请求。网络较慢时用户也能立即看到反馈，
    // 不会因为等待请求而误以为点击未生效并反复点击。
    setConfigTarget({ platform, connection: current, loading: needsConnection });
    setBusyKey(key);
    try {
      let connection = current;
      if (needsConnection) {
        connection = await createConnection({
          name: platform.label,
          platform: platform.code,
          config: {},
        });
        const saved = { ...connection, generated_tokens: null };
        setItems((previous) => [...previous, saved]);
        connection = saved;
      }
      setConfigTarget((target) =>
        target?.platform.code === platform.code
          ? { platform, connection, loading: false }
          : target,
      );
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "打开连接配置失败", "error");
      setConfigTarget((target) =>
        target?.platform.code === platform.code ? null : target,
      );
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
      void openConnection(platform, item);
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
    if (item.desired_running) {
      notify("请先关闭连接后再删除", "info");
      return;
    }
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

  const saveConnection = (saved: ImConnection) => {
    setItems((previous) => {
      const exists = previous.some((item) => item.id === saved.id);
      return exists
        ? previous.map((item) => (item.id === saved.id ? saved : item))
        : [...previous, saved];
    });
  };

  return (
    <div className="flex h-[calc(100dvh-6rem)] min-h-0 flex-col gap-4 overflow-hidden sm:h-[calc(100dvh-7rem)] lg:h-[calc(100dvh-7.5rem)]">
      <PageHeader
        title="连接 IM"
        description={
          isAdmin
            ? "当前仅显示本账号连接；全部连接请到「IM 连接监管」。"
            : undefined
        }
        actions={
          isAdmin
            ? undefined
            : (
              <Button variant="ghost" loading={checking} onClick={() => void inspectConnections()}>
                <Icon name="refresh" className="h-4 w-4" />
                检查连接
              </Button>
            )
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
          <div className="space-y-3 pb-1" aria-busy={loading}>
            {loading && displayPlatforms.length === 0 ? (
              <ConnectionListSkeleton />
            ) : (
              displayPlatforms.map((platform) => {
                const connection = connectionMap.get(platform.code) ?? null;
                const key = connection?.id ?? platform.code;
                const adminMode = isAdmin;
                return (
                  <ConnectionPlatformPanel
                    key={platform.code}
                    platform={platform}
                    connection={connection}
                    busy={busyKey === key}
                    readOnly={adminMode}
                    onToggle={adminMode ? () => {} : () => void toggle(platform, connection)}
                    onPrimaryAction={adminMode ? () => {} : () => void openConnection(platform, connection)}
                    onDelete={() => connection && void remove(connection)}
                  />
                );
              })
            )}

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
          loadingConnection={configTarget.loading}
          onClose={() => {
            setConfigTarget(null);
            void load();
          }}
          onSaved={saveConnection}
        />
      )}

      {healthReport && (
        <Modal
          title="IM 连接检查结果"
          description={`检查 ${healthReport.length} 个连接；异常连接已关闭。`}
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

    </div>
  );
}

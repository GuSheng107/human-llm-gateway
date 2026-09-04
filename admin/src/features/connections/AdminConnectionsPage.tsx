import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { notify } from "../../components/feedback/Toast";
import {
  adminListConnections,
  deleteConnection,
  listPlatforms,
  stopConnection,
  type ConnectionFilter,
} from "../../api/connections";
import type { ImConnection, PlatformSpec } from "../../types/gateway";

const DEFAULT_PAGE_SIZE = 20;

const STATE_OPTIONS = [
  ["", "全部状态"],
  ["stopped", "已停止"],
  ["starting", "启动中"],
  ["running", "运行中"],
  ["error", "异常"],
] as const;

export function AdminConnectionsPage() {
  const [items, setItems] = useState<ImConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformSpec[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [state, setState] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const platformMap = useMemo(
    () => new Map(platforms.map((platform) => [platform.code, platform])),
    [platforms],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const filters: ConnectionFilter = {};
      if (platform) filters.platform = platform;
      if (state) filters.state = state;
      const [list, platformList] = await Promise.all([
        adminListConnections(page, search, filters, pageSize),
        listPlatforms(),
      ]);
      setItems(list.items);
      setTotal(list.total);
      setPlatforms(platformList);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search, platform, state]);

  const changePageSize = (value: number) => {
    setPage(1);
    setPageSize(value);
  };

  useEffect(() => void load(), [load]);

  const stop = async (connection: ImConnection) => {
    if (!(await confirmAction({ message: `确定关闭连接「${connection.name}」？` }))) return;
    setBusyId(connection.id);
    try {
      await stopConnection(connection.id);
      notify("连接已关闭", "success");
      void load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (connection: ImConnection) => {
    if (
      !(await confirmAction({
        message: `确定删除连接「${connection.name}」？该操作不可恢复。`,
        variant: "danger",
      }))
    )
      return;
    setBusyId(connection.id);
    try {
      await deleteConnection(connection.id);
      notify("连接已删除", "success");
      void load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="IM 连接监管"
        description="可停用或删除连接，不能查看凭据。"
        actions={
          <Button variant="ghost" loading={loading} onClick={() => void load()}>
            <Icon name="refresh" className="h-4 w-4" />
            立即刷新
          </Button>
        }
      />
      {error && <ErrorBanner message={error} />}

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setSearch(input);
              setPage(1);
            }}
          >
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="搜索连接名称或用户"
              className="field-input w-48"
            />
            <Button type="submit" variant="ghost">
              搜索
            </Button>
          </form>
          <select
            value={platform}
            onChange={(event) => {
              setPlatform(event.target.value);
              setPage(1);
            }}
            className="field-input w-36"
          >
            <option value="">全部平台</option>
            {platforms.map((platform) => (
              <option key={platform.code} value={platform.code}>
                {platform.label}
              </option>
            ))}
          </select>
          <select
            value={state}
            onChange={(event) => {
              setState(event.target.value);
              setPage(1);
            }}
            className="field-input w-36"
          >
            {STATE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-left text-slate-400">
                <th className="px-4 py-3 font-medium">连接名称</th>
                <th className="px-4 py-3 font-medium">平台</th>
                <th className="px-4 py-3 font-medium">所属用户</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">绑定</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((connection) => (
                <tr key={connection.id} className="text-slate-600">
                  <td className="max-w-40 px-4 py-3">
                    <span className="block truncate font-medium text-slate-700">
                      {connection.name}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {platformMap.get(connection.platform)?.label ?? connection.platform_label}
                  </td>
                  <td className="px-4 py-3">{connection.owner_username ?? "-"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={connection.state} />
                  </td>
                  <td className="px-4 py-3">
                    {connection.bound ? (
                      <span className="text-slate-600">已绑定</span>
                    ) : (
                      <span className="text-slate-400">未绑定</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="space-x-3">
                      <button
                        disabled={busyId === connection.id}
                        onClick={() => void stop(connection)}
                        className="text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        关闭
                      </button>
                      <button
                        disabled={busyId === connection.id}
                        onClick={() => void remove(connection)}
                        className="text-red-500 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        删除
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-slate-400">
                    暂无连接
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} total={total} pageSize={pageSize} onChange={setPage} onPageSizeChange={changePageSize} />
        </div>
      </Card>
    </div>
  );
}

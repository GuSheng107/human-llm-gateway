import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  applyConnection,
  bindingStatus,
  connectionHealth,
  createBinding,
  deleteConnection,
  listConnections,
  listPlatforms,
  startConnection,
  stopConnection,
} from "../../api/connections";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { useAuth } from "../auth/AuthContext";
import type {
  BindingStatus as BindingStatusType,
  ConnectionHealth,
  ImConnection,
  PlatformSpec,
} from "../../types/gateway";
import { ConnectionFormModal } from "./ConnectionFormModal";
import { QrLoginDrawer } from "./QrLoginDrawer";

const PAGE_SIZE = 20;

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "-";
}

export function ConnectionsPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<ImConnection[]>([]);
  const [platforms, setPlatforms] = useState<PlatformSpec[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<ImConnection | null | undefined>(undefined);
  const [qrConnection, setQrConnection] = useState<ImConnection | null>(null);
  const [health, setHealth] = useState<ConnectionHealth | null>(null);
  const [binding, setBinding] = useState<BindingStatusType | null>(null);
  const [bindingCode, setBindingCode] = useState("");
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, platformList] = await Promise.all([
        listConnections(page, search),
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
  }, [page, search]);

  useEffect(() => void load(), [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const toggle = async (item: ImConnection) => {
    try {
      if (item.desired_running) {
        await stopConnection(item.id);
      } else {
        await startConnection(item.id);
      }
      notify(item.desired_running ? "连接已停止" : "连接已启动");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败");
    }
  };

  const remove = async (item: ImConnection) => {
    if (!window.confirm(`确认删除连接「${item.name}」？`)) return;
    try {
      await deleteConnection(item.id);
      notify("连接已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  const showHealth = async (item: ImConnection) => {
    try {
      setHealth(await connectionHealth(item.id));
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "获取健康状态失败");
    }
  };

  const generateBinding = async (item: ImConnection) => {
    try {
      const created = await createBinding(item.id);
      setBindingCode(created.binding_code);
      setBinding(await bindingStatus(item.id));
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "生成绑定码失败");
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="连接 IM"
        dismissId="connections"
        actions={
          !isAdmin && (
            <Button onClick={() => setEditing(null)} disabled={platforms.length === 0}>
              <Icon name="plus" className="h-4 w-4" />
              新建连接
            </Button>
          )
        }
      />

      <Card>
        <form onSubmit={submitSearch} className="flex gap-2 border-b border-slate-100 p-4">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className="field-input min-w-0 flex-1 sm:max-w-sm"
            placeholder="搜索连接名称"
          />
          <Button variant="ghost" type="submit">
            <Icon name="search" className="h-3.5 w-3.5" />
            搜索
          </Button>
        </form>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[880px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">平台</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">绑定</th>
                {isAdmin && <th className="px-4 py-3 font-medium">所有者</th>}
                <th className="px-4 py-3 font-medium">重试</th>
                <th className="px-4 py-3 font-medium">最近健康</th>
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{item.name}</td>
                  <td className="px-4 py-3 text-slate-500">{item.platform_label}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.state} fallback={item.state} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.bound ? "bound" : "unbound"} />
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-slate-500">{item.owner_username ?? "-"}</td>
                  )}
                  <td className="px-4 py-3 text-slate-500">{item.retry_count}</td>
                  <td className="px-4 py-3 text-slate-500">{formatTime(item.last_health_at)}</td>
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => void toggle(item)} className="text-primary">
                      {item.desired_running ? "停止" : "启动"}
                    </button>
                    <button onClick={() => void applyConnection(item.id).then(load)} className="text-primary">
                      应用
                    </button>
                    <button onClick={() => void showHealth(item)} className="text-primary">
                      检查
                    </button>
                    {!isAdmin && (
                      <>
                        {platforms
                          .find((platform) => platform.code === item.platform)
                          ?.supports_login && (
                          <button onClick={() => setQrConnection(item)} className="text-primary">
                            扫码
                          </button>
                        )}
                        <button onClick={() => void generateBinding(item)} className="text-primary">
                          绑定
                        </button>
                        <button onClick={() => setEditing(item)} className="text-primary">
                          编辑
                        </button>
                      </>
                    )}
                    <button onClick={() => void remove(item)} className="text-red-500">
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={isAdmin ? 8 : 7} className="px-4 py-12 text-center text-slate-400">
                    暂无连接
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>

      {editing !== undefined && (
        <ConnectionFormModal
          platforms={platforms}
          connection={editing ?? undefined}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined);
            void load();
          }}
        />
      )}

      {health && (
        <Modal title="连接健康" description="保存的状态与当前运行情况" onClose={() => setHealth(null)}>
          <div className="space-y-3 p-6 text-xs text-slate-600">
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
              <span>状态</span>
              <StatusBadge status={health.state} fallback={health.state} />
            </div>
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
              <span>期望运行</span>
              <span>{health.desired_running ? "是" : "否"}</span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
              <span>运行时在线</span>
              <span>{health.runtime.running ? "是" : "否"}</span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
              <span>连续重试次数</span>
              <span>{health.retry_count}</span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
              <span>下次重试</span>
              <span>{formatTime(health.next_retry_at)}</span>
            </div>
            {health.last_error_code && (
              <p className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-amber-700">
                {health.last_error_code}：{health.last_error_message}
              </p>
            )}
          </div>
        </Modal>
      )}

      {bindingCode && (
        <Modal
          title="一次性绑定码"
          description="关闭后不再显示，请在 IM 中发送该绑定码完成绑定。"
          onClose={() => setBindingCode("")}
        >
          <div className="space-y-4 p-6">
            <div className="break-all rounded-md border border-blue-100 bg-blue-50 p-4 text-center font-mono text-lg tracking-widest text-blue-700">
              {bindingCode}
            </div>
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>
                绑定状态：{binding?.bound ? "已绑定" : binding?.binding_pending ? "等待绑定" : "未绑定"}
              </span>
              <Button
                onClick={() => void copyText(bindingCode, "绑定码")}
              >
                <Icon name="copy" className="h-4 w-4" />
                复制
              </Button>
            </div>
          </div>
        </Modal>
      )}

      <QrLoginDrawer
        connection={qrConnection}
        onClose={() => setQrConnection(null)}
        onSaved={() => {
          notify("扫码登录成功，凭据已保存");
          void load();
        }}
      />
    </div>
  );
}

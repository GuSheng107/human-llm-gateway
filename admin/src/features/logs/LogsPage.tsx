import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  listAppLogs,
  listAuditLogs,
  type AppLogItem,
  type AuditLogItem,
} from "../../api/logs";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";

const PAGE_SIZE = 20;

type Tab = "audit" | "app";

const RESULT_BADGE: Record<string, string> = {
  success: "active",
  denied: "failed",
  failed: "failed",
};

const LEVEL_BADGE: Record<string, string> = {
  debug: "inactive",
  info: "active",
  warning: "pending_restart",
  error: "failed",
};

function formatTime(value: string): string {
  return value ? new Date(value).toLocaleString() : "-";
}

export function LogsPage() {
  const [tab, setTab] = useState<Tab>("audit");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [auditItems, setAuditItems] = useState<AuditLogItem[]>([]);
  const [appItems, setAppItems] = useState<AppLogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // 审计筛选
  const [auditAction, setAuditAction] = useState("");
  const [auditResource, setAuditResource] = useState("");
  const [auditHours, setAuditHours] = useState("");
  // 应用筛选
  const [appLevel, setAppLevel] = useState("");
  const [appEvent, setAppEvent] = useState("");
  const [appHours, setAppHours] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (tab === "audit") {
        const result = await listAuditLogs({
          page,
          action: auditAction.trim() || undefined,
          resource_type: auditResource.trim() || undefined,
          hours: auditHours ? Number(auditHours) : undefined,
        });
        setAuditItems(result.items);
        setTotal(result.total);
      } else {
        const result = await listAppLogs({
          page,
          level: appLevel || undefined,
          event: appEvent.trim() || undefined,
          hours: appHours ? Number(appHours) : undefined,
        });
        setAppItems(result.items);
        setTotal(result.total);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [tab, page, auditAction, auditResource, auditHours, appLevel, appEvent, appHours]);

  useEffect(() => {
    void load();
  }, [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    void load();
  };

  return (
    <div className="space-y-5">
      <PageHeader title="日志审计" />

      <Card>
        <div className="flex gap-1 border-b border-slate-100 px-4 pt-3">
          {(
            [
              { key: "audit", label: "审计日志" },
              { key: "app", label: "应用日志" },
            ] as const
          ).map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => {
                setTab(item.key);
                setPage(1);
              }}
              className={
                tab === item.key
                  ? "-mb-px border-b-2 border-primary px-3 py-2 text-xs font-medium text-primary"
                  : "px-3 py-2 text-xs text-slate-400 hover:text-slate-600"
              }
            >
              {item.label}
            </button>
          ))}
        </div>

        {error && <ErrorBanner message={error} className="m-4" />}

        <form onSubmit={submitSearch} className="flex flex-wrap gap-2 border-b border-slate-100 p-4">
          {tab === "audit" ? (
            <>
              <input
                value={auditAction}
                onChange={(event) => setAuditAction(event.target.value)}
                className="field-input min-w-0 flex-1 sm:max-w-[220px]"
                placeholder="动作（如 api_key.created）"
              />
              <input
                value={auditResource}
                onChange={(event) => setAuditResource(event.target.value)}
                className="field-input min-w-0 flex-1 sm:max-w-[180px]"
                placeholder="资源类型（如 request_task）"
              />
            </>
          ) : (
            <>
              <select
                value={appLevel}
                onChange={(event) => setAppLevel(event.target.value)}
                className="field-input sm:w-32"
              >
                <option value="">全部级别</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
                <option value="error">error</option>
              </select>
              <input
                value={appEvent}
                onChange={(event) => setAppEvent(event.target.value)}
                className="field-input min-w-0 flex-1 sm:max-w-[240px]"
                placeholder="事件（如 inference.human_timeout）"
              />
            </>
          )}
          <input
            type="number"
            min={1}
            max={720}
            value={appHours ? appHours : auditHours}
            onChange={(event) =>
              tab === "audit"
                ? setAuditHours(event.target.value)
                : setAppHours(event.target.value)
            }
            className="field-input sm:w-28"
            placeholder="近 N 小时"
          />
          <Button variant="ghost" type="submit">
            <Icon name="search" className="h-3.5 w-3.5" />
            筛选
          </Button>
        </form>

        <div className="overflow-x-auto">
          {tab === "audit" ? (
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">时间</th>
                  <th className="px-4 py-3 font-medium">操作者</th>
                  <th className="px-4 py-3 font-medium">动作</th>
                  <th className="px-4 py-3 font-medium">资源</th>
                  <th className="px-4 py-3 font-medium">变更字段</th>
                  <th className="px-4 py-3 font-medium">结果</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {auditItems.map((item) => (
                  <tr key={item.id} className="hover:bg-slate-50/60">
                    <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                      {formatTime(item.created_at)}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {item.actor_username ?? "-"}
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-600">{item.action}</td>
                    <td className="px-4 py-3 font-mono text-slate-500">
                      {item.resource_type}
                      {item.resource_id ? `#${item.resource_id}` : ""}
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-slate-400">
                      {item.fields.length ? item.fields.join("、") : "-"}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge
                        status={RESULT_BADGE[item.result] ?? "inactive"}
                        fallback={item.result}
                      />
                    </td>
                  </tr>
                ))}
                {!loading && auditItems.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-slate-400">
                      暂无审计记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          ) : (
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">时间</th>
                  <th className="px-4 py-3 font-medium">级别</th>
                  <th className="px-4 py-3 font-medium">事件</th>
                  <th className="px-4 py-3 font-medium">消息</th>
                  <th className="px-4 py-3 font-medium">关联</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {appItems.map((item) => (
                  <tr key={item.id} className="hover:bg-slate-50/60">
                    <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                      {formatTime(item.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge
                        status={LEVEL_BADGE[item.level] ?? "inactive"}
                        fallback={item.level}
                      />
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-600">{item.event || "-"}</td>
                    <td className="max-w-[320px] truncate px-4 py-3 text-slate-500">
                      {item.message}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-[11px] text-slate-400">
                      {[
                        item.request_id ? `req:${item.request_id.slice(0, 12)}` : null,
                        item.task_id ? `task:${item.task_id}` : null,
                        item.user_id ? `user:${item.user_id}` : null,
                      ]
                        .filter(Boolean)
                        .join(" ") || "-"}
                    </td>
                  </tr>
                ))}
                {!loading && appItems.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-12 text-center text-slate-400">
                      暂无应用日志
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>
    </div>
  );
}
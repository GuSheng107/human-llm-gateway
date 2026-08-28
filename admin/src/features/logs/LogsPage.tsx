import { useCallback, useEffect, useState } from "react";
import { listAppLogs, listAuditLogs } from "../../api/tasks";
import { notify } from "../../components/feedback/Toast";
import { Pagination } from "../../components/data-display/Pagination";
import type { AppLogItem, AuditLogItem } from "../../types/tasks";

const LEVEL_COLORS: Record<string, string> = {
  info: "bg-blue-50 text-blue-600",
  warning: "bg-amber-50 text-amber-600",
  error: "bg-red-50 text-red-600",
  critical: "bg-red-100 text-red-700",
};

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN");
}

export function LogsPage() {
  const [tab, setTab] = useState<"audit" | "app">("audit");

  const [auditItems, setAuditItems] = useState<AuditLogItem[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditPage, setAuditPage] = useState(1);
  const [auditAction, setAuditAction] = useState("");

  const [appItems, setAppItems] = useState<AppLogItem[]>([]);
  const [appTotal, setAppTotal] = useState(0);
  const [appPage, setAppPage] = useState(1);
  const [appLevel, setAppLevel] = useState("");
  const [appSearch, setAppSearch] = useState("");
  const [appStart, setAppStart] = useState("");
  const [appEnd, setAppEnd] = useState("");
  const [loading, setLoading] = useState(true);

  const loadAudit = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listAuditLogs({
        page: auditPage, page_size: 20,
        action: auditAction || undefined,
      });
      setAuditItems(result.items);
      setAuditTotal(result.total);
    } catch (error) {
      notify(error instanceof Error ? error.message : "审计日志加载失败");
    } finally {
      setLoading(false);
    }
  }, [auditPage, auditAction]);

  const loadApp = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listAppLogs({
        page: appPage, page_size: 20,
        level: appLevel || undefined,
        search: appSearch || undefined,
        start: appStart ? new Date(appStart).toISOString() : undefined,
        end: appEnd ? new Date(appEnd).toISOString() : undefined,
      });
      setAppItems(result.items);
      setAppTotal(result.total);
    } catch (error) {
      notify(error instanceof Error ? error.message : "运行日志加载失败");
    } finally {
      setLoading(false);
    }
  }, [appPage, appLevel, appSearch, appStart, appEnd]);

  useEffect(() => {
    if (tab === "audit") void loadAudit();
    else void loadApp();
  }, [tab, loadAudit, loadApp]);

  return (
    <div className="mx-auto max-w-[1300px] space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">日志与审计</h1>
            <p className="mt-2 text-xs text-slate-400">审计日志记录业务操作；运行日志记录连接器异常、LLM 调用失败等技术事件。</p>
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={() => setTab("audit")} className={`rounded-md px-3 py-2 text-xs ${tab === "audit" ? "bg-[#409eff] text-white" : "border border-slate-200 text-slate-500"}`}>审计日志</button>
            <button type="button" onClick={() => setTab("app")} className={`rounded-md px-3 py-2 text-xs ${tab === "app" ? "bg-[#409eff] text-white" : "border border-slate-200 text-slate-500"}`}>运行日志</button>
          </div>
        </div>
      </section>

      {tab === "audit" && (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <input
              value={auditAction}
              onChange={(event) => { setAuditAction(event.target.value); setAuditPage(1); }}
              placeholder="按 action 筛选，如 connector.bound"
              className="h-9 w-72 rounded-md border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-[#409eff] focus:bg-white"
            />
            <Pagination page={auditPage} pageSize={20} total={auditTotal} onChange={setAuditPage} />
          </div>
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
                <th className="px-5 py-3">时间</th>
                <th className="px-4 py-3">动作</th>
                <th className="px-4 py-3">对象</th>
                <th className="px-4 py-3">操作者</th>
                <th className="px-5 py-3">详情</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={5} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
              ) : auditItems.length === 0 ? (
                <tr><td colSpan={5} className="px-5 py-16 text-center text-xs text-slate-400">暂无记录</td></tr>
              ) : auditItems.map((item) => (
                <tr key={item.id} className="text-xs">
                  <td className="whitespace-nowrap px-5 py-3 text-slate-500">{formatTime(item.created_at)}</td>
                  <td className="px-4 py-3"><code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">{item.action}</code></td>
                  <td className="px-4 py-3 text-slate-600">{item.subject_type} #{item.subject_id}</td>
                  <td className="px-4 py-3 text-slate-600">{item.actor}</td>
                  <td className="max-w-72 truncate px-5 py-3 font-mono text-[10px] text-slate-400" title={item.detail}>{item.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === "app" && (
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-4">
            <select value={appLevel} onChange={(event) => { setAppLevel(event.target.value); setAppPage(1); }} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600">
              <option value="">全部级别</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
              <option value="critical">critical</option>
            </select>
            <input type="datetime-local" value={appStart} onChange={(event) => { setAppStart(event.target.value); setAppPage(1); }} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600" />
            <span className="text-[10px] text-slate-400">至</span>
            <input type="datetime-local" value={appEnd} onChange={(event) => { setAppEnd(event.target.value); setAppPage(1); }} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600" />
            <input
              value={appSearch}
              onChange={(event) => { setAppSearch(event.target.value); setAppPage(1); }}
              placeholder="搜索消息…"
              className="h-9 w-48 rounded-md border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-[#409eff] focus:bg-white"
            />
            <div className="ml-auto">
              <Pagination page={appPage} pageSize={20} total={appTotal} onChange={setAppPage} />
            </div>
          </div>
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="bg-slate-50 text-[11px] font-medium text-slate-500">
                <th className="px-5 py-3">时间</th>
                <th className="px-4 py-3">级别</th>
                <th className="px-4 py-3">来源</th>
                <th className="px-5 py-3">消息</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={4} className="px-5 py-16 text-center text-xs text-slate-400">加载中…</td></tr>
              ) : appItems.length === 0 ? (
                <tr><td colSpan={4} className="px-5 py-16 text-center text-xs text-slate-400">暂无记录</td></tr>
              ) : appItems.map((item) => (
                <tr key={item.id} className="text-xs">
                  <td className="whitespace-nowrap px-5 py-3 text-slate-500">{formatTime(item.created_at)}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] ${LEVEL_COLORS[item.level] ?? "bg-slate-100 text-slate-500"}`}>{item.level}</span>
                  </td>
                  <td className="px-4 py-3 font-mono text-[10px] text-slate-500">{item.logger}</td>
                  <td className="max-w-96 truncate px-5 py-3 text-slate-700" title={item.message}>{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

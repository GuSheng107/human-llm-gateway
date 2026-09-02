import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  createInvitation,
  deleteInvitation,
  listInvitations,
  revokeInvitation,
  updateInvitation,
  type InvitationPayload,
} from "../../api/invitations";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import type { Invitation, InvitationCreated } from "../../types/governance";
import { InvitationFormModal } from "./InvitationFormModal";

const DEFAULT_PAGE_SIZE = 20;

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "永久有效";
}

export function InvitationsPage() {
  const [items, setItems] = useState<Invitation[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Invitation | null | undefined>(undefined);
  const [created, setCreated] = useState<InvitationCreated | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listInvitations(page, search, pageSize);
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search]);

  const changePageSize = (value: number) => {
    setPage(1);
    setPageSize(value);
  };

  useEffect(() => void load(), [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const save = async (payload: InvitationPayload) => {
    if (editing) {
      await updateInvitation(editing.id, payload);
      notify("邀请码已更新");
    } else {
      setCreated(await createInvitation(payload));
    }
    setEditing(undefined);
    await load();
  };

  const revoke = async (item: Invitation) => {
    if (!(await confirmAction({ message: `确认撤销邀请码 ${item.note || item.code_prefix}…？撤销后立即不可使用。`, confirmLabel: "确认撤销" }))) return;
    await revokeInvitation(item.id);
    notify("邀请码已撤销");
    await load();
  };

  const remove = async (item: Invitation) => {
    if (!(await confirmAction({ message: `确认删除已撤销的邀请码 ${item.note || item.code_prefix}…？` }))) return;
    await deleteInvitation(item.id);
    notify("邀请码已删除");
    await load();
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="邀请码管理"
        actions={
          <Button onClick={() => setEditing(null)}>
            <Icon name="plus" className="h-4 w-4" />
            创建邀请码
          </Button>
        }
      />

      <Card>
        <form onSubmit={submitSearch} className="flex gap-2 border-b border-slate-100 p-4">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className="field-input min-w-0 flex-1 sm:max-w-sm"
            placeholder="搜索邀请码名称"
          />
          <Button variant="ghost" type="submit">
            <Icon name="search" className="h-3.5 w-3.5" />
            搜索
          </Button>
        </form>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[640px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">邀请码名称</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">使用次数</th>
                <th className="px-4 py-3 font-medium">过期时间</th>
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.id} className="group hover:bg-slate-50/60">
                  <td className="max-w-xs truncate px-4 py-3 text-slate-700">{item.note || "-"}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {item.used_count} / {item.max_uses}
                  </td>
                  <td className="px-4 py-3 text-slate-500">{formatDate(item.expires_at)}</td>
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button onClick={() => setEditing(item)} className="text-primary">
                      编辑
                    </button>
                    {item.status !== "revoked" ? (
                      <button onClick={() => void revoke(item)} className="text-red-500">
                        撤销
                      </button>
                    ) : (
                      <button onClick={() => void remove(item)} className="text-red-500">
                        删除
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-400">
                    暂无邀请码
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={pageSize} total={total} onChange={setPage} onPageSizeChange={changePageSize} />
        </div>
      </Card>

      {editing !== undefined && (
        <InvitationFormModal invitation={editing ?? undefined} onClose={() => setEditing(undefined)} onSubmit={save} />
      )}
      {created && (
        <Modal title="邀请码已创建" description="关闭后不再显示，请立即复制。" onClose={() => setCreated(null)}>
          <div className="space-y-4 p-6">
            <div className="break-all rounded-md border border-blue-100 bg-blue-50 p-4 font-mono text-sm text-blue-700">
              {created.code}
            </div>
            <div className="flex justify-end">
              <Button
                onClick={() =>
                  void copyText(created.code, "邀请码").then((result) => {
                    if (result.ok) setCreated(null);
                  })
                }
              >
                <Icon name="copy" className="h-4 w-4" />
                复制
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

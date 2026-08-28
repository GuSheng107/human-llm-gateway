import { type FormEvent, useState } from "react";
import type { InvitationPayload } from "../../api/invitations";
import { Modal } from "../../components/feedback/Modal";
import type { Invitation } from "../../types/governance";

function localInput(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function InvitationFormModal({
  invitation,
  onClose,
  onSubmit,
}: {
  invitation?: Invitation;
  onClose: () => void;
  onSubmit: (payload: InvitationPayload) => Promise<void>;
}) {
  const [note, setNote] = useState(invitation?.note ?? "");
  const [maxUses, setMaxUses] = useState(invitation?.max_uses ?? 1);
  const [expiresAt, setExpiresAt] = useState(localInput(invitation?.expires_at ?? null));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({
        note: note.trim() || null,
        max_uses: maxUses,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title={invitation ? "编辑邀请码" : "创建邀请码"} description="邀请码明文仅在创建成功后显示一次。" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4 p-6">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">备注</span>
          <input value={note} maxLength={255} onChange={(event) => setNote(event.target.value)} className="field-input" placeholder="例如：测试团队" />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">最大使用次数</span>
            <input required min={1} type="number" value={maxUses} onChange={(event) => setMaxUses(Number(event.target.value))} className="field-input" />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">过期时间</span>
            <input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} className="field-input" />
          </label>
        </div>
        {error && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
          <button type="button" onClick={onClose} className="rounded-md border border-slate-200 px-4 py-2 text-xs text-slate-500">取消</button>
          <button disabled={submitting} className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white disabled:opacity-60">{submitting ? "正在保存…" : "保存"}</button>
        </div>
      </form>
    </Modal>
  );
}

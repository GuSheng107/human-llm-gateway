import { type FormEvent, useState } from "react";
import type { InvitationPayload } from "../../api/invitations";
import { FormField } from "../../components/form/FormField";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";
import type { Invitation } from "../../types/governance";

const MAX_USES = 1000;

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

  const originalExpiresAt = invitation?.expires_at ?? null;
  const maxUsesInvalid = maxUses < 1 || maxUses > MAX_USES;
  // 过期时间选填：留空表示永久有效；仅当填写了新时间且早于当前时才报错。
  const expiresInvalid =
    expiresAt !== "" &&
    expiresAt !== localInput(originalExpiresAt) &&
    new Date(expiresAt).getTime() <= Date.now();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (maxUsesInvalid) {
      setError(`最大使用次数需在 1 到 ${MAX_USES} 之间`);
      return;
    }
    if (expiresInvalid) {
      setError("过期时间需晚于当前时间，或留空表示永久有效");
      return;
    }
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
    <Modal
      title={invitation ? "编辑邀请码" : "创建邀请码"}
      description="邀请码明文仅在创建成功后显示一次。"
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-4 p-6">
        <FormField label="备注">
          <input
            value={note}
            maxLength={255}
            onChange={(event) => setNote(event.target.value)}
            className="field-input"
            placeholder="例如：测试团队"
          />
        </FormField>
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="最大使用次数"
            required
            error={maxUsesInvalid ? `需在 1 到 ${MAX_USES} 之间` : undefined}
          >
            <input
              required
              min={1}
              max={MAX_USES}
              type="number"
              value={maxUses}
              onChange={(event) => setMaxUses(Number(event.target.value))}
              className="field-input"
            />
          </FormField>
          <FormField
            label="过期时间"
            hint="留空表示永久有效"
            error={expiresInvalid ? "需晚于当前时间，或留空" : undefined}
          >
            <input
              type="datetime-local"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
              className="field-input"
            />
          </FormField>
        </div>
        {error && <ErrorBanner message={error} />}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" loading={submitting}>
            {submitting ? "正在保存…" : "保存"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

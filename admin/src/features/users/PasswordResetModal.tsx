import { type FormEvent, useState } from "react";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";
import type { UserSummary } from "../../types/governance";

export function PasswordResetModal({
  user,
  onClose,
  onSubmit,
}: {
  user: UserSummary;
  onClose: () => void;
  onSubmit: (password?: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit(password || undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重置失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={`重置 ${user.username} 的密码`}
      description="现有登录会话将立即撤销，用户下次登录必须修改临时密码。"
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-4 p-6">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">临时密码（可选）</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="field-input"
            placeholder="留空则由系统生成"
          />
        </label>
        {error && <ErrorBanner message={error} />}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="danger" loading={submitting}>
            {submitting ? "正在重置…" : "确认重置"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

import { type FormEvent, useState } from "react";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";

export function UserCreateModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: { username: string; display_name: string; password?: string }) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({
        username: username.trim(),
        display_name: displayName.trim(),
        ...(password ? { password } : {}),
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="创建普通用户" description="新用户首次登录必须修改临时密码。" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4 p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">登录账号</span>
            <input
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="field-input"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-600">显示名</span>
            <input
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              className="field-input"
            />
          </label>
        </div>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-slate-600">临时密码（可选）</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="field-input"
            placeholder="留空则由系统生成"
          />
        </label>
        <p className="text-caption leading-5 text-slate-400">留空则由系统生成，仅显示一次。</p>
        {error && <ErrorBanner message={error} />}
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" loading={submitting}>
            {submitting ? "正在创建…" : "创建用户"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

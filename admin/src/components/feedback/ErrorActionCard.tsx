import { ApiError } from "../../api/client";

const ACTION_LABELS: Record<string, string> = {
  relogin: "重新登录",
  rescan: "重新扫码",
  regenerate_binding: "重新生成绑定码",
  retry_start: "重试启动",
  apply_config: "应用配置",
  wait_and_retry: "稍后重试",
  view_logs: "查看日志",
  contact_admin: "联系管理员",
  fix_input: "修正输入",
};

interface Props {
  error: ApiError | Error;
  onAction?: (action: string) => void;
}

export function ErrorActionCard({ error, onAction }: Props) {
  const apiError = error instanceof ApiError ? error : null;
  const action = apiError?.action ?? "none";
  const hasAction = action !== "none" && Boolean(ACTION_LABELS[action]);

  return (
    <div className="rounded-md border border-red-100 bg-red-50 px-4 py-3">
      <div className="text-xs leading-5 text-red-600">{error.message}</div>
      <div className="mt-2 flex items-center gap-3">
        {apiError && hasAction && onAction && (
          <button
            type="button"
            onClick={() => onAction(action)}
            className="rounded border border-red-200 bg-white px-2.5 py-1 text-[11px] text-red-500 transition hover:bg-red-100"
          >
            {ACTION_LABELS[action]}
          </button>
        )}
        {apiError?.requestId && (
          <span className="font-mono text-[10px] text-red-300">req: {apiError.requestId.slice(0, 16)}…</span>
        )}
      </div>
    </div>
  );
}

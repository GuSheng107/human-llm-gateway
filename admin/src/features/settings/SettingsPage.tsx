import { type FormEvent, useCallback, useEffect, useState } from "react";
import { getSettings, updateSettings } from "../../api/settings";
import { notify } from "../../components/feedback/Toast";

const SETTING_LABELS: Record<string, { label: string; hint: string }> = {
  human_timeout_seconds: { label: "人工回复超时（秒）", hint: "超过该时长未收到人工回复则任务超时" },
  binding_code_ttl_seconds: { label: "绑定码有效期（秒）", hint: "生成绑定码后的可用时长" },
  binding_max_attempts: { label: "绑定失败上限", hint: "连续失败该次数后锁定绑定" },
  circuit_breaker_threshold: { label: "熔断阈值", hint: "连续失败该次数后触发熔断" },
  circuit_breaker_cooldown_seconds: { label: "熔断冷却（秒）", hint: "锁定后的等待时长" },
  allow_plain_human_reply: { label: "允许纯文本人工回复", hint: "开启后无需完整 DSL 也能回复" },
  stream_chunk_size: { label: "流式分块大小", hint: "模拟流式输出的每块字符数" },
  stream_delay_min_ms: { label: "流式最小延迟（ms）", hint: "块间最小延迟" },
  stream_delay_max_ms: { label: "流式最大延迟（ms）", hint: "块间最大延迟" },
};

export function SettingsPage() {
  const [items, setItems] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const result = await getSettings();
    setItems(result.items);
    setDraft(result.items);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const result = await updateSettings(draft);
      setItems(result.items);
      notify("设置已更新并即刻生效");
    } catch (error) {
      notify(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (!items) {
    return <div className="py-16 text-center text-xs text-slate-400">加载中…</div>;
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-2xl space-y-5">
      <section className="rounded-lg border border-slate-200 bg-white px-5 py-6 shadow-sm">
        <h1 className="text-lg font-semibold text-slate-800">基础设置</h1>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          运行时参数，保存后即刻生效（无需重启服务）。应用级机密（app_secret、数据库地址）仍需环境变量并在重启后生效。
        </p>
        <div className="mt-6 space-y-5">
          {Object.entries(SETTING_LABELS).map(([key, meta]) => (
            <label key={key} className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">{meta.label}</span>
              {typeof items[key] === "boolean" ? (
                <select
                  value={draft[key] ? "true" : "false"}
                  onChange={(event) => setDraft({ ...draft, [key]: event.target.value === "true" })}
                  className="field-input"
                >
                  <option value="true">开启</option>
                  <option value="false">关闭</option>
                </select>
              ) : (
                <input
                  type="number"
                  value={String(draft[key] ?? "")}
                  onChange={(event) => setDraft({ ...draft, [key]: Number(event.target.value) })}
                  className="field-input"
                />
              )}
              <p className="mt-1.5 text-[11px] text-slate-400">{meta.hint}</p>
            </label>
          ))}
        </div>
        <div className="mt-6 flex justify-end border-t border-slate-100 pt-4">
          <button
            disabled={saving}
            className="rounded-md bg-[#409eff] px-4 py-2 text-xs font-medium text-white hover:bg-[#337ecc] disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存设置"}
          </button>
        </div>
      </section>
    </form>
  );
}

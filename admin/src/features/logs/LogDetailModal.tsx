import { useCallback, useEffect, useState } from "react";
import { getLogDetail, type LogDetail, type LogDetailSection } from "../../api/logs";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { copyText } from "../../utils/clipboard";
import { friendlyErrorMessage } from "../../utils/notify";

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function sectionText(section: LogDetailSection): string {
  if (section.format === "json") {
    return typeof section.data === "string"
      ? section.data
      : JSON.stringify(section.data ?? null, null, 2);
  }
  return String(section.data ?? "");
}

/** section 渲染：json 为对象树，text 为纯文本，jsonl 按行渲染。 */
function SectionView({ section }: { section: LogDetailSection }) {
  const text = sectionText(section);
  const isJson = section.format === "json";
  return (
    <section className="rounded-lg border border-slate-200">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-xs font-medium text-slate-700">{section.title || section.key}</span>
        <div className="flex items-center gap-2">
          {section.redacted_fields && section.redacted_fields.length > 0 && (
            <span
              className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"
              title="已脱敏字段"
            >
              脱敏 {section.redacted_fields.length} 字段
            </span>
          )}
          <button
            type="button"
            className="text-[11px] text-primary hover:underline"
            onClick={() => void copyText(text, section.title || section.key)}
          >
            复制
          </button>
        </div>
      </div>
      <pre
        className={`max-h-80 overflow-auto whitespace-pre-wrap p-3 font-mono text-[11px] leading-5 ${
          isJson ? "text-slate-600" : "text-slate-600"
        }`}
      >
        {text || "(空)"}
      </pre>
    </section>
  );
}

export interface LogDetailModalProps {
  entryId: string;
  /** 打开时的列表行信息（用于首屏标题），详情懒加载。 */
  title?: string;
  onClose: () => void;
  /** 按 trace 跳转（同链路时间线视图）。 */
  onFilterByTrace?: (traceId: string) => void;
}

export function LogDetailModal({ entryId, title, onClose, onFilterByTrace }: LogDetailModalProps) {
  const [detail, setDetail] = useState<LogDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setDetail(await getLogDetail(entryId));
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "加载日志详情失败"));
    } finally {
      setLoading(false);
    }
  }, [entryId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Modal
      title="日志详情"
      description={title || detail?.event || ""}
      onClose={onClose}
      width="max-w-4xl"
    >
      <div className="max-h-[78vh] space-y-4 overflow-y-auto p-6 text-xs">
        {error && <ErrorBanner message={error} />}
        {loading && !detail && (
          <div className="grid min-h-40 place-items-center text-slate-400">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
          </div>
        )}
        {detail && (
          <>
            <dl className="grid gap-2 sm:grid-cols-3">
              <div className="rounded border border-slate-100 bg-slate-50/70 px-3 py-2">
                <dt className="text-[10px] text-slate-400">事件</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-slate-700">
                  {detail.event || "-"}
                </dd>
              </div>
              <div className="rounded border border-slate-100 bg-slate-50/70 px-3 py-2">
                <dt className="text-[10px] text-slate-400">Trace ID</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-slate-700">
                  {detail.request_id ? (
                    <button
                      type="button"
                      className="text-primary hover:underline"
                      onClick={() => onFilterByTrace?.(detail.request_id ?? "")}
                      title="查看同 trace 链路"
                    >
                      {detail.request_id}
                    </button>
                  ) : (
                    "-"
                  )}
                </dd>
              </div>
              <div className="rounded border border-slate-100 bg-slate-50/70 px-3 py-2">
                <dt className="text-[10px] text-slate-400">详情大小</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-slate-700">
                  {formatBytes(detail.detail_size_bytes ?? 0)}
                  {detail.detail_truncated && (
                    <span className="ml-1 text-amber-600" title="超出防御上限被丢弃">
                      （已截断）
                    </span>
                  )}
                </dd>
              </div>
            </dl>

            <section className="rounded-lg border border-slate-200">
              <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                <span className="text-xs font-medium text-slate-700">消息</span>
                <button
                  type="button"
                  className="text-[11px] text-primary hover:underline"
                  onClick={() => void copyText(detail.message ?? "", "消息")}
                >
                  复制
                </button>
              </div>
              <p className="whitespace-pre-wrap p-3 text-slate-600">
                {detail.message || "-"}
              </p>
            </section>

            {detail.context && Object.keys(detail.context).length > 0 && (
              <section className="rounded-lg border border-slate-200">
                <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                  <span className="text-xs font-medium text-slate-700">结构化字段</span>
                  <button
                    type="button"
                    className="text-[11px] text-primary hover:underline"
                    onClick={() =>
                      void copyText(JSON.stringify(detail.context, null, 2), "结构化字段")
                    }
                  >
                    复制
                  </button>
                </div>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap p-3 font-mono text-[11px] leading-5 text-slate-600">
                  {JSON.stringify(detail.context, null, 2)}
                </pre>
              </section>
            )}

            {detail.detail?.sections && detail.detail.sections.length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Icon name="list" className="h-3.5 w-3.5 text-slate-400" />
                  <span className="text-xs font-medium text-slate-700">
                    诊断详情（{detail.detail.sections.length} 段）
                  </span>
                  {detail.detail.schema_version && (
                    <span className="text-[10px] text-slate-400">
                      envelope v{detail.detail.schema_version}
                    </span>
                  )}
                </div>
                {detail.detail.sections.map((section) => (
                  <SectionView key={section.key} section={section} />
                ))}
                {detail.detail.links && detail.detail.links.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 px-1">
                    <span className="text-[10px] text-slate-400">关联：</span>
                    {detail.detail.links.map((link) => (
                      <button
                        key={`${link.kind}-${link.id}`}
                        type="button"
                        className="rounded-full border border-primary-ghost bg-primary-faint px-2 py-0.5 text-[10px] text-primary hover:underline"
                        onClick={() => onFilterByTrace?.(detail.request_id ?? "")}
                        title={`跳转到关联 ${link.label}`}
                      >
                        {link.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {!detail.detail && detail.kind === "app" && (
              <p className="rounded-md border border-dashed border-slate-200 px-3 py-3 text-center text-[11px] text-slate-400">
                该条日志没有详情信封（仅结构化字段）。
              </p>
            )}
          </>
        )}
      </div>
      {loading && detail && (
        <div className="border-t border-slate-100 px-6 py-2 text-[11px] text-slate-400">
          刷新中…
        </div>
      )}
    </Modal>
  );
}

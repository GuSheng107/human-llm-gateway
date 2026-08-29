import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  createLlmConfig,
  deleteLlmConfig,
  listLlmConfigs,
  testLlmConfig,
  updateLlmConfig,
  type LlmConfigHeaderInput,
  type LlmConfigPayload,
  type LlmConfigUpdatePayload,
  type LlmProtocol,
} from "../../api/llmConfigs";
import { Card } from "../../components/data-display/Card";
import { Pagination } from "../../components/data-display/Pagination";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import type { LlmConfig } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";

const PAGE_SIZE = 20;

const PROTOCOL_LABEL: Record<LlmProtocol, string> = {
  openai_compatible: "OpenAI 兼容",
  anthropic: "Anthropic",
};

interface LlmFormState {
  id?: string;
  name: string;
  protocol: LlmProtocol;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  headers: LlmConfigHeaderInput[];
  enabled: boolean;
}

const blankForm = (): LlmFormState => ({
  name: "",
  protocol: "openai_compatible",
  base_url: "",
  api_key: "",
  model: "",
  timeout_seconds: 120,
  headers: [],
  enabled: true,
});

export function LlmConfigsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState<LlmConfig[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<LlmFormState | null>(null);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    configId: string;
    success: boolean;
    reason_code: string;
    detail: string;
    http_status: number | null;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listLlmConfigs(page, search);
      setItems(result.items);
      setTotal(result.total);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => void load(), [load]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(input.trim());
  };

  const openCreate = () => {
    setFormError("");
    setForm(blankForm());
  };

  const openEdit = (cfg: LlmConfig) => {
    setFormError("");
    setForm({
      id: cfg.id,
      name: cfg.name,
      protocol: cfg.protocol,
      base_url: cfg.base_url,
      api_key: "",
      model: cfg.real_model,
      timeout_seconds: cfg.timeout_seconds,
      headers: cfg.headers.map((header) => ({ name: header.name, value: "" })),
      enabled: cfg.is_enabled,
    });
  };

  const submit = async () => {
    if (!form) return;
    const payload: LlmConfigPayload | LlmConfigUpdatePayload = form.id
      ? {
          ...(form.name.trim() ? { name: form.name.trim() } : {}),
          protocol: form.protocol,
          base_url: form.base_url.trim(),
          model: form.model.trim(),
          timeout_seconds: form.timeout_seconds,
          headers: form.headers,
          enabled: form.enabled,
          ...(form.api_key.trim() ? { api_key: form.api_key } : {}),
        }
      : {
          name: form.name.trim(),
          protocol: form.protocol,
          base_url: form.base_url.trim(),
          api_key: form.api_key,
          model: form.model.trim(),
          timeout_seconds: form.timeout_seconds,
          headers: form.headers,
          enabled: form.enabled,
        };
    setSaving(true);
    setFormError("");
    try {
      if (form.id) {
        await updateLlmConfig(form.id, payload as LlmConfigUpdatePayload);
        notify("LLM 配置已更新");
      } else {
        await createLlmConfig(payload as LlmConfigPayload);
        notify("LLM 配置已创建");
      }
      setForm(null);
      await load();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (cfg: LlmConfig) => {
    if (!window.confirm(`确认删除 LLM 配置「${cfg.name}」？`)) return;
    try {
      await deleteLlmConfig(cfg.id);
      notify("LLM 配置已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  const runTest = async (cfg: LlmConfig) => {
    setTestingId(cfg.id);
    setTestResult(null);
    try {
      const outcome = await testLlmConfig(cfg.id);
      setTestResult({
        configId: cfg.id,
        success: outcome.success,
        reason_code: outcome.reason_code,
        detail: outcome.detail,
        http_status: outcome.http_status,
      });
      notify(outcome.success ? "连通性测试成功" : "连通性测试失败");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "测试失败");
    } finally {
      setTestingId(null);
    }
  };

  const updateHeader = (index: number, patch: Partial<LlmConfigHeaderInput>) => {
    if (!form) return;
    setForm({
      ...form,
      headers: form.headers.map((header, i) => (i === index ? { ...header, ...patch } : header)),
    });
  };

  const addHeader = () => {
    if (!form) return;
    setForm({ ...form, headers: [...form.headers, { name: "", value: "" }] });
  };

  const removeHeader = (index: number) => {
    if (!form) return;
    setForm({ ...form, headers: form.headers.filter((_, i) => i !== index) });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="LLM 管理"
        description="维护真实 LLM 配置：协议、Base URL、模型、超时与自定义 Header。"
      />

      <Card>
        <form
          onSubmit={submitSearch}
          className="flex flex-wrap gap-2 border-b border-slate-100 p-4"
        >
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            className="field-input min-w-0 flex-1 sm:max-w-sm"
            placeholder="搜索名称、模型或 Base URL"
          />
          <Button variant="ghost" type="submit">
            <Icon name="search" className="h-3.5 w-3.5" />
            搜索
          </Button>
          <Button onClick={openCreate}>
            <Icon name="plus" className="h-4 w-4" />
            新建配置
          </Button>
        </form>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="min-w-[960px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">协议</th>
                <th className="px-4 py-3 font-medium">Base URL</th>
                <th className="px-4 py-3 font-medium">真实模型</th>
                <th className="px-4 py-3 font-medium">密钥</th>
                <th className="px-4 py-3 font-medium">自定义头</th>
                <th className="px-4 py-3 font-medium">最近测试</th>
                <th className="px-4 py-3 font-medium">状态</th>
                {isAdmin && <th className="px-4 py-3 font-medium">所有者</th>}
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((cfg) => (
                <tr key={cfg.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{cfg.name}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {PROTOCOL_LABEL[cfg.protocol] ?? cfg.protocol}
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-500">{cfg.base_url_host}</td>
                  <td className="px-4 py-3 font-mono text-slate-500">{cfg.real_model}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {cfg.api_key_set ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-700">
                        <Icon name="check" className="h-3 w-3" />
                        已设置
                      </span>
                    ) : (
                      <span className="text-slate-400">未设置</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {cfg.headers.length === 0 ? (
                      <span className="text-slate-400">—</span>
                    ) : (
                      cfg.headers
                        .map((header) => header.name)
                        .join("、")
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {cfg.last_tested_at ? (
                      <span
                        className={
                          cfg.last_test_result === "success"
                            ? "text-emerald-600"
                            : cfg.last_test_result === "failed"
                              ? "text-red-600"
                              : "text-slate-500"
                        }
                      >
                        {cfg.last_test_result === "success"
                          ? "成功"
                          : cfg.last_test_result === "failed"
                            ? "失败"
                            : cfg.last_test_result ?? "—"}
                      </span>
                    ) : (
                      <span className="text-slate-400">未测试</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={cfg.is_enabled ? "active" : "inactive"} />
                  </td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-slate-500">{cfg.owner_username ?? "-"}</td>
                  )}
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button
                      onClick={() => void runTest(cfg)}
                      disabled={testingId === cfg.id}
                      className="text-primary disabled:text-slate-400"
                    >
                      {testingId === cfg.id ? "测试中…" : "测试"}
                    </button>
                    <button onClick={() => openEdit(cfg)} className="text-primary">
                      编辑
                    </button>
                    <button onClick={() => void remove(cfg)} className="text-red-500">
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td
                    colSpan={isAdmin ? 10 : 9}
                    className="px-4 py-12 text-center text-slate-400"
                  >
                    暂无 LLM 配置
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end border-t border-slate-100 px-4 py-3">
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </div>
      </Card>

      {form && (
        <Modal
          title={form.id ? "编辑 LLM 配置" : "新建 LLM 配置"}
          description="Secret 与自定义 Header 仅在写入时记录，列表和详情不回显。"
          onClose={() => setForm(null)}
        >
          <div className="space-y-4 p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">名称</span>
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                className="field-input"
                maxLength={100}
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">协议</span>
                <select
                  value={form.protocol}
                  onChange={(event) =>
                    setForm({ ...form, protocol: event.target.value as LlmProtocol })
                  }
                  className="field-input"
                >
                  <option value="openai_compatible">OpenAI 兼容</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  超时（秒，5-600）
                </span>
                <input
                  type="number"
                  min={5}
                  max={600}
                  value={form.timeout_seconds}
                  onChange={(event) =>
                    setForm({ ...form, timeout_seconds: Number(event.target.value) })
                  }
                  className="field-input"
                />
              </label>
            </div>

            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                Base URL（http/https）
              </span>
              <input
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                className="field-input font-mono"
                placeholder="https://api.example.com/v1"
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  真实模型
                </span>
                <input
                  value={form.model}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                  className="field-input font-mono"
                  placeholder="gpt-4o-mini / claude-3-5-sonnet"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  API Key
                  {form.id && (
                    <span className="ml-2 text-slate-400">
                      （留空表示保留旧值）
                    </span>
                  )}
                </span>
                <input
                  type="password"
                  value={form.api_key}
                  onChange={(event) => setForm({ ...form, api_key: event.target.value })}
                  className="field-input font-mono"
                  placeholder={form.id ? "保留旧值" : "sk-..."}
                  autoComplete="off"
                />
              </label>
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50/60 p-4">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-medium text-slate-600">
                  自定义 Header（不包含 Authorization）
                </p>
                <Button variant="ghost" type="button" onClick={addHeader}>
                  <Icon name="plus" className="h-3 w-3" />
                  添加
                </Button>
              </div>
              {form.headers.length === 0 ? (
                <p className="text-xs text-slate-400">无</p>
              ) : (
                <div className="space-y-2">
                  {form.headers.map((header, index) => (
                    <div key={index} className="flex gap-2">
                      <input
                        value={header.name}
                        onChange={(event) => updateHeader(index, { name: event.target.value })}
                        placeholder="X-Header-Name"
                        className="field-input w-1/3 font-mono"
                      />
                      <input
                        value={header.value}
                        onChange={(event) => updateHeader(index, { value: event.target.value })}
                        placeholder="value"
                        className="field-input flex-1 font-mono"
                      />
                      <Button
                        variant="ghost"
                        type="button"
                        onClick={() => removeHeader(index)}
                        aria-label="删除自定义 Header"
                      >
                        <Icon name="close" className="h-3 w-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              />
              启用（停用后无法发起测试与转发）
            </label>

            {formError && (
              <p className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-600">
                {formError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setForm(null)}>
                取消
              </Button>
              <Button
                onClick={() => void submit()}
                loading={saving}
                disabled={
                  !form.name.trim() ||
                  !form.base_url.trim() ||
                  !form.model.trim() ||
                  (!form.id && !form.api_key.trim())
                }
              >
                <Icon name="check" className="h-4 w-4" />
                保存
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {testResult && (
        <Modal
          title={testResult.success ? "连通性测试成功" : "连通性测试失败"}
          description={`reason_code: ${testResult.reason_code}`}
          onClose={() => setTestResult(null)}
        >
          <div className="space-y-3 p-6 text-xs">
            <div className="flex items-center gap-2">
              <StatusBadge status={testResult.success ? "active" : "error"} />
              <span className="text-slate-500">{testResult.detail}</span>
            </div>
            {testResult.http_status !== null && (
              <div className="text-slate-500">
                上游 HTTP 状态：{testResult.http_status}
              </div>
            )}
            <div className="flex justify-end">
              <Button variant="ghost" onClick={() => setTestResult(null)}>
                关闭
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
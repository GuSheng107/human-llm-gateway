import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  createLlmConfig,
  deleteLlmConfig,
  listLlmConfigs,
  testLlmConfig,
  updateLlmConfig,
  type LlmConfigPayload,
  type LlmConfigUpdatePayload,
  type LlmProtocol,
  type ThinkingLevel,
  type ThinkingMode,
} from "../../api/llmConfigs";
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
import type { LlmConfig } from "../../types/gateway";
import { useAuth } from "../auth/AuthContext";

const PAGE_SIZE = 20;

const PROTOCOL_LABEL: Record<LlmProtocol, string> = {
  openai_chat: "OpenAI Chat Completions",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
};

const BASE_URL_PLACEHOLDER: Record<LlmProtocol, string> = {
  openai_chat: "https://api.openai.com/v1",
  openai_responses: "https://api.openai.com/v1",
  anthropic_messages: "https://api.anthropic.com/v1",
};

interface LlmFormState {
  id?: string;
  name: string;
  protocol: LlmProtocol;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  enabled: boolean;
  full_url: boolean;
  default_temperature: string;
  default_top_p: string;
  default_top_k: string;
  context_window_input: string;
  context_window_output: string;
  max_tool_call_rounds: number;
  supports_image_input: boolean;
  thinking_mode: ThinkingMode;
  thinking_level: ThinkingLevel | "";
  extra_body_text: string;
}

const blankForm = (): LlmFormState => ({
  name: "",
  protocol: "openai_chat",
  base_url: "",
  api_key: "",
  model: "",
  timeout_seconds: 120,
  enabled: true,
  full_url: false,
  default_temperature: "",
  default_top_p: "",
  default_top_k: "",
  context_window_input: "",
  context_window_output: "",
  max_tool_call_rounds: 16,
  supports_image_input: false,
  thinking_mode: "model_default",
  thinking_level: "",
  extra_body_text: "{}",
});

function optionalNumber(value: string): number | null {
  return value.trim() ? Number(value) : null;
}

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
    if (isAdmin) return;
    setFormError("");
    setForm(blankForm());
  };

  const openEdit = (cfg: LlmConfig) => {
    if (isAdmin) return;
    setFormError("");
    setForm({
      id: cfg.id,
      name: cfg.name,
      protocol: cfg.protocol,
      base_url: cfg.base_url,
      api_key: "",
      model: cfg.real_model,
      timeout_seconds: cfg.timeout_seconds,
      enabled: cfg.is_enabled,
      full_url: /\/(chat\/completions|responses|messages)\/?$/.test(cfg.base_url),
      default_temperature: cfg.default_temperature?.toString() ?? "",
      default_top_p: cfg.default_top_p?.toString() ?? "",
      default_top_k: cfg.default_top_k?.toString() ?? "",
      context_window_input: cfg.context_window_input?.toString() ?? "",
      context_window_output: cfg.context_window_output?.toString() ?? "",
      max_tool_call_rounds: cfg.max_tool_call_rounds,
      supports_image_input: cfg.supports_image_input,
      thinking_mode: cfg.thinking_mode,
      thinking_level: cfg.thinking_level ?? "",
      extra_body_text: JSON.stringify(cfg.extra_body ?? {}, null, 2),
    });
  };

  const submit = async () => {
    if (!form || isAdmin) return;
    const temperature = optionalNumber(form.default_temperature);
    const topP = optionalNumber(form.default_top_p);
    const topK = optionalNumber(form.default_top_k);
    if (temperature !== null && (temperature < 0 || temperature > 2)) {
      setFormError("Temperature 必须在 0 到 2 之间");
      return;
    }
    if (topP !== null && (topP < 0 || topP > 1)) {
      setFormError("Top P 必须在 0 到 1 之间");
      return;
    }
    if (topK !== null && (!Number.isInteger(topK) || topK < 1 || topK > 100)) {
      setFormError("Top K 必须是 1 到 100 的整数");
      return;
    }
    if (
      form.protocol === "openai_responses" &&
      form.thinking_mode === "enabled" &&
      !form.thinking_level
    ) {
      setFormError("OpenAI Responses 开启思考模式时请选择思考等级");
      return;
    }
    let extraBody: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(form.extra_body_text || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("Extra Body 必须是 JSON 对象");
      }
      extraBody = parsed as Record<string, unknown>;
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "Extra Body 不是合法 JSON");
      return;
    }
    const advanced = {
      default_temperature: temperature,
      default_top_p: topP,
      default_top_k: topK,
      context_window_input: optionalNumber(form.context_window_input),
      context_window_output: optionalNumber(form.context_window_output),
      max_tool_call_rounds: form.max_tool_call_rounds,
      supports_image_input: form.supports_image_input,
      thinking_mode: form.thinking_mode,
      thinking_level:
        form.protocol === "openai_responses" && form.thinking_mode === "enabled"
          ? form.thinking_level || null
          : null,
      extra_body: extraBody,
    };
    const payload: LlmConfigPayload | LlmConfigUpdatePayload = form.id
      ? {
          ...(form.name.trim() ? { name: form.name.trim() } : {}),
          protocol: form.protocol,
          base_url: form.base_url.trim(),
          model: form.model.trim(),
          timeout_seconds: form.timeout_seconds,
          enabled: form.enabled,
          ...advanced,
          ...(form.api_key.trim() ? { api_key: form.api_key } : {}),
        }
      : {
          name: form.name.trim(),
          protocol: form.protocol,
          base_url: form.base_url.trim(),
          api_key: form.api_key,
          model: form.model.trim(),
          timeout_seconds: form.timeout_seconds,
          enabled: form.enabled,
          ...advanced,
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
    if (isAdmin) return;
    if (!(await confirmAction({ message: `确认删除 LLM 配置「${cfg.name}」？` }))) return;
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

  return (
    <div className="space-y-5">
      <PageHeader
        title="LLM 管理"
      />

      {isAdmin && (
        <div
          role="status"
          className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800"
        >
          管理员视角 · 只读。管理员可以监管配置状态，但不能创建、编辑、测试或删除用户的
          LLM 配置。
        </div>
      )}

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
          <Button
            onClick={openCreate}
            disabled={isAdmin}
            title={isAdmin ? "管理员不能创建 LLM 配置" : undefined}
          >
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
                    {isAdmin ? (
                      <span className="text-slate-400">只读</span>
                    ) : (
                      <>
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
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td
                    colSpan={isAdmin ? 9 : 8}
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

      {form && !isAdmin && (
        <Modal
          title={form.id ? "编辑 LLM 配置" : "新建 LLM 配置"}
          onClose={() => setForm(null)}
          width="max-w-3xl"
        >
          <div className="max-h-[82vh] space-y-4 overflow-y-auto p-6">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                名称<span className="ml-0.5 text-danger">*</span>
              </span>
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
                  onChange={(event) => {
                    const protocol = event.target.value as LlmProtocol;
                    setForm({
                      ...form,
                      protocol,
                      thinking_level: protocol === "openai_responses" ? form.thinking_level : "",
                    });
                  }}
                  className="field-input"
                >
                  <option value="openai_chat">OpenAI Chat Completions 格式</option>
                  <option value="openai_responses">OpenAI Responses 格式</option>
                  <option value="anthropic_messages">Anthropic Messages 格式</option>
                </select>
                <label className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
                  <input
                    type="checkbox"
                    checked={form.full_url}
                    onChange={(event) => setForm({ ...form, full_url: event.target.checked })}
                  />
                  直接填写完整端点 URL
                </label>
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
                  {form.full_url ? "完整请求 URL" : "API Base URL"}
                  <span className="ml-0.5 text-danger">*</span>
                </span>
              <input
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                className="field-input font-mono"
                placeholder={
                  form.full_url
                    ? `${BASE_URL_PLACEHOLDER[form.protocol]}/${
                        form.protocol === "openai_chat"
                          ? "chat/completions"
                          : form.protocol === "openai_responses"
                            ? "responses"
                            : "messages"
                      }`
                    : BASE_URL_PLACEHOLDER[form.protocol]
                }
              />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  真实模型<span className="ml-0.5 text-danger">*</span>
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
                  API Key<span className="ml-0.5 text-danger">*</span>
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

            <details open className="rounded-lg border border-slate-200 bg-slate-50/50">
              <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold text-slate-700">
                高级配置
              </summary>
              <div className="space-y-5 border-t border-slate-200 p-4">
                <section>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-medium text-slate-600">上下文窗口（Token）</p>
                    <div className="flex flex-wrap gap-1">
                      {[
                        ["128K", 131072],
                        ["256K", 262144],
                        ["512K", 524288],
                        ["1M", 1048576],
                      ].map(([label, value]) => (
                        <button
                          key={label}
                          type="button"
                          onClick={() =>
                            setForm({ ...form, context_window_input: String(value) })
                          }
                          className="rounded border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 hover:border-primary/40 hover:text-primary"
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block text-[11px] text-slate-500">
                      输入窗口
                      <input
                        type="number"
                        min={1}
                        value={form.context_window_input}
                        onChange={(event) =>
                          setForm({ ...form, context_window_input: event.target.value })
                        }
                        className="field-input mt-1"
                        placeholder="留空跟随模型"
                      />
                    </label>
                    <label className="block text-[11px] text-slate-500">
                      输出窗口
                      <input
                        type="number"
                        min={1}
                        value={form.context_window_output}
                        onChange={(event) =>
                          setForm({ ...form, context_window_output: event.target.value })
                        }
                        className="field-input mt-1"
                        placeholder="留空跟随模型"
                      />
                    </label>
                  </div>
                </section>

                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block text-xs font-medium text-slate-600">
                    工具调用最大轮数
                    <input
                      type="number"
                      min={1}
                      max={500}
                      value={form.max_tool_call_rounds}
                      onChange={(event) =>
                        setForm({ ...form, max_tool_call_rounds: Number(event.target.value) })
                      }
                      className="field-input mt-1.5"
                    />
                  </label>
                  <label className="block text-xs font-medium text-slate-600">
                    图片输入
                    <select
                      value={form.supports_image_input ? "yes" : "no"}
                      onChange={(event) =>
                        setForm({ ...form, supports_image_input: event.target.value === "yes" })
                      }
                      className="field-input mt-1.5"
                    >
                      <option value="no">不支持</option>
                      <option value="yes">支持</option>
                    </select>
                  </label>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block text-xs font-medium text-slate-600">
                    思考模式
                    <select
                      value={form.thinking_mode}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          thinking_mode: event.target.value as ThinkingMode,
                          thinking_level:
                            event.target.value === "enabled" ? form.thinking_level : "",
                        })
                      }
                      className="field-input mt-1.5"
                    >
                      <option value="model_default">跟随模型默认</option>
                      <option value="enabled">开启</option>
                      <option value="disabled">关闭</option>
                    </select>
                  </label>
                  {form.thinking_mode === "enabled" ? (
                    <label className="block text-xs font-medium text-slate-600">
                      思考等级
                      <select
                        value={form.thinking_level}
                        onChange={(event) =>
                          setForm({ ...form, thinking_level: event.target.value as ThinkingLevel })
                        }
                        className="field-input mt-1.5"
                      >
                        <option value="">请选择</option>
                        <option value="minimal">Minimal</option>
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                        <option value="xhigh">XHigh</option>
                        <option value="max">Max</option>
                      </select>
                    </label>
                  ) : (
                    <div className="rounded-md border border-dashed border-slate-200 bg-white px-3 py-2 text-[11px] leading-5 text-slate-400">
                      三种上游格式都支持思考模式：OpenAI 两种格式映射 reasoning effort，
                      Anthropic 按等级映射思考预算。
                    </div>
                  )}
                </div>

                <section>
                  <p className="mb-2 text-xs font-medium text-slate-600">默认采样参数</p>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {[
                      ["Temperature", "default_temperature", "0-2"],
                      ["Top P", "default_top_p", "0-1"],
                      ["Top K", "default_top_k", "1-100"],
                    ].map(([label, key, placeholder]) => (
                      <label key={key} className="block text-[11px] text-slate-500">
                        {label}
                        <input
                          type="number"
                          step={key === "default_top_k" ? 1 : 0.01}
                          value={form[key as keyof Pick<LlmFormState, "default_temperature" | "default_top_p" | "default_top_k">]}
                          onChange={(event) =>
                            setForm({ ...form, [key]: event.target.value })
                          }
                          className="field-input mt-1"
                          placeholder={placeholder}
                        />
                      </label>
                    ))}
                  </div>
                </section>

                <label className="block text-xs font-medium text-slate-600">
                  Extra Body（JSON 对象）
                  <textarea
                    value={form.extra_body_text}
                    onChange={(event) => setForm({ ...form, extra_body_text: event.target.value })}
                    className="field-input mt-1.5 min-h-28 font-mono text-[11px]"
                    spellCheck={false}
                  />
                </label>
              </div>
            </details>

            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              />
              启用（停用后不能测试和转发）
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

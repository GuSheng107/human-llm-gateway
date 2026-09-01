import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  createTool,
  deleteTool,
  executeTool,
  listTools,
  updateTool,
  type ToolExecutionItem,
  type ToolItem,
} from "../../api/tools";
import { Card } from "../../components/data-display/Card";
import { StatusBadge } from "../../components/data-display/StatusBadge";
import { ErrorBanner } from "../../components/feedback/ErrorBanner";
import { confirmAction } from "../../components/feedback/ConfirmDialog";
import { Modal } from "../../components/feedback/Modal";
import { notify } from "../../components/feedback/Toast";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { Icon } from "../../icons";
import { useAuth } from "../auth/AuthContext";

const EXECUTION_BADGE: Record<string, string> = {
  succeeded: "active",
  failed: "failed",
  timed_out: "timeout",
  limit_exceeded: "pending_restart",
  running: "connecting",
};

export function ToolsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // 管理员表单
  const [form, setForm] = useState<{
    id?: string;
    name: string;
    description: string;
    command_template: string;
    args_text: string;
    timeout_seconds: number;
  } | null>(null);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  // 用户执行
  const [execTarget, setExecTarget] = useState<ToolItem | null>(null);
  const [execArgs, setExecArgs] = useState<Record<string, string>>({});
  const [execResult, setExecResult] = useState<ToolExecutionItem | null>(null);
  const [executing, setExecuting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const toolPage = await listTools(1);
      setTools(toolPage.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submitTool = async () => {
    if (!form) return;
    let schema: import("../../api/tools").ToolArgumentsSchema;
    try {
      schema = JSON.parse(form.args_text || '{"type":"object","properties":{}}');
    } catch {
      setFormError("参数 Schema 不是合法 JSON");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        command_template: form.command_template.trim(),
        arguments_schema: schema,
        timeout_seconds: form.timeout_seconds,
      };
      if (form.id) {
        await updateTool(form.id, payload);
        notify("工具已更新");
      } else {
        await createTool(payload);
        notify("工具已创建");
      }
      setForm(null);
      await load();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleTool = async (tool: ToolItem) => {
    try {
      await updateTool(tool.id, { is_enabled: !tool.is_enabled });
      notify(tool.is_enabled ? "工具已停用" : "工具已启用");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "操作失败");
    }
  };

  const removeTool = async (tool: ToolItem) => {
    if (!(await confirmAction({ message: `确认删除工具「${tool.name}」？` }))) return;
    try {
      await deleteTool(tool.id);
      notify("工具已删除");
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "删除失败");
    }
  };

  const openExec = (tool: ToolItem) => {
    const args: Record<string, string> = {};
    Object.keys(tool.arguments_schema?.properties ?? {}).forEach((key) => {
      args[key] = "";
    });
    setExecArgs(args);
    setExecResult(null);
    setExecTarget(tool);
  };

  const runTool = async () => {
    if (!execTarget) return;
    setExecuting(true);
    try {
      const result = await executeTool(execTarget.id, execArgs);
      setExecResult(result);
      notify(result.state === "succeeded" ? "执行成功" : `执行结束：${result.state}`);
      await load();
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "执行失败");
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="工具沙箱"
        actions={
          isAdmin ? (
            <Button
              onClick={() =>
                setForm({
                  name: "",
                  description: "",
                  command_template: "",
                  args_text: '{"type":"object","properties":{}}',
                  timeout_seconds: 30,
                })
              }
            >
              <Icon name="plus" className="h-4 w-4" />
              新建工具
            </Button>
          ) : undefined
        }
      />

      <Card>
        {error && <ErrorBanner message={error} className="m-4" />}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-xs">
            <thead className="bg-slate-50 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">说明</th>
                {isAdmin && <th className="px-4 py-3 font-medium">命令模板</th>}
                <th className="px-4 py-3 font-medium">超时</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="sticky right-0 z-10 bg-slate-50 px-4 py-3 text-right font-medium">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tools.map((tool) => (
                <tr key={tool.id} className="group hover:bg-slate-50/60">
                  <td className="px-4 py-3 font-medium text-slate-700">{tool.name}</td>
                  <td className="max-w-[220px] truncate px-4 py-3 text-slate-500">
                    {tool.description ?? "-"}
                  </td>
                  {isAdmin && (
                    <td className="max-w-[260px] truncate px-4 py-3 font-mono text-slate-500">
                      {tool.command_template ?? "-"}
                    </td>
                  )}
                  <td className="px-4 py-3 text-slate-500">{tool.timeout_seconds}s</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={tool.is_enabled ? "active" : "inactive"} />
                  </td>
                  <td className="sticky right-0 space-x-3 bg-white px-4 py-3 text-right group-hover:bg-slate-50">
                    <button
                      onClick={() => openExec(tool)}
                      disabled={!tool.is_enabled}
                      className="text-primary disabled:text-slate-300"
                    >
                      执行
                    </button>
                    {isAdmin && (
                      <>
                        <button onClick={() => void toggleTool(tool)} className="text-primary">
                          {tool.is_enabled ? "停用" : "启用"}
                        </button>
                        <button
                          onClick={() =>
                            setForm({
                              id: tool.id,
                              name: tool.name,
                              description: tool.description ?? "",
                              command_template: tool.command_template ?? "",
                              args_text: JSON.stringify(
                                tool.arguments_schema ?? { type: "object", properties: {} },
                                null,
                                2,
                              ),
                              timeout_seconds: tool.timeout_seconds,
                            })
                          }
                          className="text-primary"
                        >
                          编辑
                        </button>
                        <button onClick={() => void removeTool(tool)} className="text-red-500">
                          删除
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && tools.length === 0 && (
                <tr>
                  <td
                    colSpan={isAdmin ? 6 : 5}
                    className="px-4 py-12 text-center text-slate-400"
                  >
                    暂无可用工具
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {form && (
        <Modal
          title={form.id ? "编辑工具" : "新建工具"}
          description="命令模板占位符 {name} 必须与参数 Schema 声明一致；执行不经 shell。"
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
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">说明</span>
              <input
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                className="field-input"
                maxLength={500}
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">命令模板</span>
              <input
                value={form.command_template}
                onChange={(event) =>
                  setForm({ ...form, command_template: event.target.value })
                }
                className="field-input font-mono"
                placeholder="python -c &quot;print(1)&quot;"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                参数 Schema（JSON，仅 string 属性）
              </span>
              <textarea
                value={form.args_text}
                onChange={(event) => setForm({ ...form, args_text: event.target.value })}
                className="field-input min-h-[120px] font-mono text-[11px]"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-slate-600">
                超时（秒，1-120）
              </span>
              <input
                type="number"
                min={1}
                max={120}
                value={form.timeout_seconds}
                onChange={(event) =>
                  setForm({ ...form, timeout_seconds: Number(event.target.value) })
                }
                className="field-input"
              />
            </label>
            {formError && <ErrorBanner message={formError} />}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setForm(null)}>
                取消
              </Button>
              <Button onClick={() => void submitTool()} loading={saving}>
                保存
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {execTarget && (
        <Modal
          title={`执行工具 · ${execTarget.name}`}
          description="工具在隔离进程中运行（临时目录、清零环境、限时）；本次执行将写入审计。"
          onClose={() => setExecTarget(null)}
        >
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              void runTool();
            }}
            className="space-y-4 p-6"
          >
            {Object.keys(execArgs).length === 0 && (
              <p className="text-xs text-slate-400">该工具无需参数。</p>
            )}
            {Object.entries(execArgs).map(([key, value]) => (
              <label key={key} className="block">
                <span className="mb-1.5 block text-xs font-medium text-slate-600">
                  {key}
                  {(execTarget.arguments_schema?.required ?? []).includes(key) && (
                    <span className="text-red-400"> *</span>
                  )}
                </span>
                <input
                  value={value}
                  onChange={(event) =>
                    setExecArgs({ ...execArgs, [key]: event.target.value })
                  }
                  className="field-input font-mono"
                />
              </label>
            ))}
            {execResult && (
              <div className="space-y-1 rounded border border-slate-100 bg-slate-50 p-3 text-xs">
                <div className="flex items-center gap-2">
                  <StatusBadge
                    status={EXECUTION_BADGE[execResult.state] ?? "inactive"}
                    fallback={execResult.state}
                  />
                  <span className="text-slate-400">
                    退出码 {execResult.exit_code ?? "-"} · {execResult.duration_ms ?? "-"}ms
                  </span>
                </div>
                {execResult.stdout && (
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-600">
                    {execResult.stdout}
                  </pre>
                )}
                {execResult.stderr && (
                  <pre className="max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-red-500">
                    {execResult.stderr}
                  </pre>
                )}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setExecTarget(null)}>
                关闭
              </Button>
              <Button type="submit" loading={executing}>
                确认执行
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

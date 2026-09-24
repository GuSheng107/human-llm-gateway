// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { streamAssistantMessage } from "../../api/assistant";
import { listLlmConfigs } from "../../api/llmConfigs";
import { getTask, saveDraft, submitReply } from "../../api/tasks";
import { TaskEditor } from "../tasks/TaskEditor";
import { Modal } from "../../components/feedback/Modal";
import { AssistantPanel } from "./AssistantPanel";
import { registerEditBridge } from "./bridge";

const state = vi.hoisted(() => ({
  open: true,
  setOpen: vi.fn(),
  sessions: [{ id: "session1", title: "测试会话", llm_config_id: "1" }],
  activeSessionId: "session1",
  setActiveSessionId: vi.fn(),
  refreshSessions: vi.fn(async () => undefined),
}));

vi.mock("./AssistantContext", () => ({ useAssistant: () => state }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "user" } }) }));
vi.mock("../../api/llmConfigs", () => ({ listLlmConfigs: vi.fn(async () => ({ items: [{ id: "1", name: "测试模型", is_enabled: true }] })) }));
vi.mock("../../api/assistant", () => ({
  getAssistantSession: vi.fn(async () => ({ messages: [], usage: null })),
  streamAssistantMessage: vi.fn(async () => undefined),
  createAssistantSession: vi.fn(), deleteAssistantSession: vi.fn(), patchAssistantSession: vi.fn(), sendAssistantMessage: vi.fn(),
}));
vi.mock("../../api/tasks", () => ({
  getTask: vi.fn(),
  getRequestView: vi.fn(async () => ({ tool_call_warning: { required: false, acknowledged: true } })),
  acknowledgeToolCallWarning: vi.fn(), generateDraft: vi.fn(), saveDraft: vi.fn(), submitReply: vi.fn(), updateDraft: vi.fn(),
}));

function task(id: string): Awaited<ReturnType<typeof getTask>> {
  return {
    id, public_id: `task-${id}`, display_name: `任务${id}`, state: "waiting_human", protocol: "openai_chat",
    can_edit: true, tool_definitions: [], drafts: [], active_draft_id: null, result_draft: null,
    fake_model_name: "fake-model", reply_strategy: "human", delivery_mode: "web",
    human_deadline_at: null, has_tools: false,
    requested_model: "fake-model", api_key_prefix: "sk-test", api_key_name: "测试Key",
    stream_requested: false, prompt_preview: "问题", response_id: null,
    created_at: "2026-09-22T00:00:00Z", completed_at: null,
    owner_user_id: "2", owner_username: "test-user", origin_trace_id: "trace-test", is_owner: true,
    prompt_text: "问题", raw_request: null, previous_task_id: null, public_error_code: null,
    cancel_reason_code: null, events: [], events_total: 0,
  };
}

function Workbench() {
  const [taskId, setTaskId] = useState("9");
  const [editing, setEditing] = useState(true);
  const navigate = useNavigate();
  return <>
    <button onClick={() => { setTaskId("10"); navigate("/replies?focus=10"); }}>切换任务</button>
    <button onClick={() => { setEditing(false); navigate("/settings/logs?trace_id=trace-new"); }}>切换日志页</button>
    {editing && <TaskEditor taskId={taskId} />}
    <AssistantPanel />
  </>;
}

describe("工作台小助手只读交互", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.open = true;
    localStorage.clear();
    vi.mocked(getTask).mockImplementation(async (id) => task(id));
  });
  afterEach(() => { cleanup(); registerEditBridge(null); });

  it("入口打开面板，检查提示发送最新未保存草稿且不写入工作台", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/replies?focus=9"]}><Workbench /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "请小助手检查" }));
    expect(state.setOpen).toHaveBeenCalledWith(true);
    const editor = await screen.findByPlaceholderText("面向调用方的最终回复内容");
    await user.type(editor, "未保存的新版回复");
    const check = await screen.findByRole("button", { name: "检查当前草稿" });
    await waitFor(() => expect((check as HTMLButtonElement).disabled).toBe(false));
    await user.click(check);
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(streamAssistantMessage).toHaveBeenCalledOnce());
    const payload = vi.mocked(streamAssistantMessage).mock.calls[0][1];
    expect(payload.text).toContain("validate_reply_draft");
    expect(payload.page_context).toMatchObject({ feature: "replies", resource: { task_id: "9" }, unsaved_edit: { final_text: "未保存的新版回复" } });
    expect(Object.keys(payload.page_context!.resource)).toEqual(["task_id"]);
    expect(saveDraft).not.toHaveBeenCalled();
    expect(submitReply).not.toHaveBeenCalled();
    expect((editor as HTMLTextAreaElement).value).toBe("未保存的新版回复");
  });

  it("切换任务清空前一稿，日志页只发送当前trace上下文", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/replies?focus=9"]}><Workbench /></MemoryRouter>);
    await user.type(await screen.findByPlaceholderText("面向调用方的最终回复内容"), "任务9的私有草稿");
    await user.click(screen.getByRole("button", { name: "切换任务" }));
    await screen.findByText("任务10");
    expect((screen.getByPlaceholderText("面向调用方的最终回复内容") as HTMLTextAreaElement).value).toBe("");
    await user.click(screen.getByRole("button", { name: "切换日志页" }));
    const logs = await screen.findByRole("button", { name: "查看调用日志" });
    await waitFor(() => expect((logs as HTMLButtonElement).disabled).toBe(false));
    await user.click(logs);
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(streamAssistantMessage).toHaveBeenCalledOnce());
    expect(vi.mocked(streamAssistantMessage).mock.calls[0][1].page_context).toMatchObject({ feature: "logs", resource: { trace_id: "trace-new" }, unsaved_edit: null });
    expect(JSON.stringify(vi.mocked(streamAssistantMessage).mock.calls[0][1])).not.toContain("任务9的私有草稿");
  });

  it("历史会话配置失效时只读快捷入口全部禁用", async () => {
    vi.mocked(listLlmConfigs).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 });
    render(<MemoryRouter initialEntries={["/replies?focus=9"]}><Workbench /></MemoryRouter>);
    await screen.findByRole("button", { name: "请小助手检查" });
    for (const name of ["解读请求", "检查当前草稿", "查看调用日志", "发送"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
    expect(streamAssistantMessage).not.toHaveBeenCalled();
  });

  it("在回复弹窗上打开助手，Tab留在助手且Esc只关闭助手并还原焦点", async () => {
    state.open = false;
    const user = userEvent.setup();
    const closeReply = vi.fn();
    const ui = <MemoryRouter initialEntries={["/replies?focus=9"]}>
      <Modal title="回复编辑" onClose={closeReply}><TaskEditor taskId="9" /></Modal>
      <AssistantPanel />
    </MemoryRouter>;
    const { rerender } = render(ui);
    const trigger = await screen.findByRole("button", { name: "请小助手检查" });
    await user.click(trigger);
    state.open = true;
    rerender(<MemoryRouter initialEntries={["/replies?focus=9"]}>
      <Modal title="回复编辑" onClose={closeReply}><TaskEditor taskId="9" /></Modal>
      <AssistantPanel />
    </MemoryRouter>);
    const panel = await screen.findByRole("dialog", { name: "小助手面板" });
    await user.tab();
    expect(panel.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    expect(state.setOpen).toHaveBeenLastCalledWith(false);
    expect(closeReply).not.toHaveBeenCalled();
    state.open = false;
    rerender(ui);
    expect(screen.queryByRole("dialog", { name: "小助手面板" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

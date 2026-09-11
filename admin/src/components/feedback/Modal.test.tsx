// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./Modal";

afterEach(cleanup);

function Example() {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>新建</button>
    {open && <Modal title="编辑" onClose={() => setOpen(false)}>
      <input aria-label="名称" />
      <button>保存</button>
    </Modal>}
    <button>背景操作</button>
  </>;
}

it("keeps keyboard focus inside the dialog and restores it on Escape", async () => {
  const user = userEvent.setup();
  render(<Example />);
  await user.click(screen.getByRole("button", { name: "新建" }));
  expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "名称" }));
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "保存" }));
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "关闭" }));
  await user.tab({ shift: true });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "保存" }));
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "新建" }));
});

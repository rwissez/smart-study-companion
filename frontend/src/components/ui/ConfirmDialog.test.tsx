import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("默认焦点在取消，Escape 走取消而不是接受", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open title="获取帮助前确认" confirmLabel="继续获取帮助" cancelLabel="先自己再试试" onConfirm={onConfirm} onCancel={onCancel}>
        <p>获取帮助后，本次作答不再作为独立完成的证据。</p>
      </ConfirmDialog>,
    );
    const cancel = screen.getByRole("button", { name: "先自己再试试" });
    expect(cancel).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("只有明确点击接受才触发确认", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog open title="确认标记未作答" confirmLabel="确认交卷" onConfirm={onConfirm} onCancel={vi.fn()}>
        <p>确定交卷吗？</p>
      </ConfirmDialog>,
    );
    await userEvent.click(screen.getByRole("button", { name: "确认交卷" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("关闭后不渲染内容", () => {
    render(
      <ConfirmDialog open={false} title="隐藏的确认" confirmLabel="确定" onConfirm={vi.fn()} onCancel={vi.fn()}>
        <p>内容</p>
      </ConfirmDialog>,
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

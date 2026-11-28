import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Modal } from "./Modal";

function Harness({ withFooter = false }: { withFooter?: boolean }) {
  const [value, setValue] = useState("");
  return (
    <Modal
      open
      title="编辑学习方案"
      // 故意每次渲染都传新的内联回调：父组件现实中就是这样写的。
      onClose={() => undefined}
      footer={withFooter ? <button type="button">发布</button> : undefined}
    >
      <label>
        修改理由
        <input value={value} onChange={(event) => setValue(event.target.value)} />
      </label>
    </Modal>
  );
}

describe("Modal 焦点稳定", () => {
  it("父组件每次输入重渲染时不抢走输入框焦点，连续字符不会丢", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText("修改理由");
    await user.click(input);
    await user.keyboard("因为学生反馈");
    expect(input).toHaveValue("因为学生反馈");
    expect(input).toHaveFocus();
  });

  it("有底部按钮时输入同样不丢字符", async () => {
    const user = userEvent.setup();
    render(<Harness withFooter />);
    const input = screen.getByLabelText("修改理由");
    await user.click(input);
    await user.keyboard("压缩开场白后重新安排");
    expect(input).toHaveValue("压缩开场白后重新安排");
  });
});

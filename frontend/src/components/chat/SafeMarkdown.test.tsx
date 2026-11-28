import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("把标题、粗体、列表、代码与分隔线渲染成结构，而不是裸符号", () => {
    const { container } = render(
      <SafeMarkdown
        text={"## 结论\n\n**先订正符号**，再看括号。\n\n- 第一步：去括号\n- 第二步：合并同类项\n\n`-(a+b) = -a-b`\n\n---\n\n下一步：重做一道同类题。"}
      />,
    );
    expect(screen.getByRole("heading", { name: "结论" })).toBeInTheDocument();
    expect(screen.getByText("先订正符号").tagName).toBe("STRONG");
    expect(container.querySelectorAll("ul.mdList li")).toHaveLength(2);
    expect(screen.getByText("-(a+b) = -a-b").tagName).toBe("CODE");
    expect(container.querySelector("hr.mdDivider")).not.toBeNull();
    // 原始星号与井号不应直接出现在可见文本里
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("##");
  });

  it("不执行 HTML 或脚本，只按文本显示", () => {
    const { container } = render(
      <SafeMarkdown text={'<script>alert("x")</script>\n\n<img src=x onerror="alert(1)">'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/<script>alert\("x"\)<\/script>/)).toBeInTheDocument();
    expect(container.innerHTML).not.toContain("<img");
  });

  it("数学符号保持纯文本，单星号乘法与变量名不被当成强调", () => {
    const { container } = render(
      <SafeMarkdown text={"因为 a² + b² = c²，所以 √4 = 2；2*3*4 = 24，字段 content_version 不变。"} />,
    );
    expect(
      screen.getByText("因为 a² + b² = c²，所以 √4 = 2；2*3*4 = 24，字段 content_version 不变。"),
    ).toBeInTheDocument();
    expect(container.querySelector("math")).toBeNull();
    expect(container.querySelector("em")).toBeNull();
  });
});

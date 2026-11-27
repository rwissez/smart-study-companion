import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LessonRenderer, validateLesson } from "./LessonRenderer";
import type { MicroLessonView } from "../../api/types";

function lesson(components: unknown[], overrides: Partial<MicroLessonView> = {}): MicroLessonView {
  return {
    content_id: "lesson-1",
    content_version: "lessonv-1",
    schema_version: "1.0",
    title: "去括号的符号处理",
    goal: "看清符号变化",
    components: components as MicroLessonView["components"],
    safe_sources: [{ source_label: "课程要求" }],
    fallback_text: "安全回退文本：先看括号前的符号。",
    review_status: "validated",
    activity_id: "act-1",
    plan_version: "plan-1",
    ...overrides,
  };
}

const SIX_COMPONENTS = [
  { type: "text", id: "t1", heading: "规则", paragraphs: ["括号前是负号，去掉括号每一项都变号。", "先看括号前的符号，再逐项检查。"] },
  { type: "formula", id: "f1", expr: "-(2x-7) = -2x+7", caption: "负号乘进括号" },
  {
    type: "derivation",
    id: "d1",
    mode: "equation",
    steps: [
      { expr: "3x+8=20", note: "原式" },
      { expr: "3x=12", note: "两边减 8" },
      { expr: "x=4", note: "两边除以 3" },
    ],
  },
  { type: "contrast", id: "c1", before: "看两道写法", incorrect: "-(2x-7)=-2x-7", correct: "-(2x-7)=-2x+7", note: "第二项要变号" },
  {
    type: "number_line",
    id: "n1",
    min: "0",
    max: "6",
    tick: "1",
    sets: [
      { label: "集合 A", intervals: [{ lo: "2", hi: "4", lo_closed: true, hi_closed: false }] },
      { label: "集合 B", intervals: [{ lo: "1", hi: null, lo_closed: false, hi_closed: false }] },
      { label: "交集", intervals: [{ lo: "2", hi: "4", lo_closed: true, hi_closed: false }] },
    ],
    operation: "intersection",
    result_index: 2,
  },
  { type: "citation", id: "z1", labels: [{ label: "课程要求", source_index: 0 }] },
];

describe("LessonRenderer", () => {
  it("六类固定组件都能安全渲染，数轴用文字标注开闭", () => {
    const { container } = render(<LessonRenderer lesson={lesson(SIX_COMPONENTS)} />);
    expect(screen.getByText("括号前是负号，去掉括号每一项都变号。")).toBeInTheDocument();
    expect(screen.getByLabelText("公式 -(2x-7) = -2x+7")).toBeInTheDocument();
    expect(screen.getByText("等式变形")).toBeInTheDocument();
    expect(screen.getByText("错误做法")).toBeInTheDocument();
    expect(screen.getByText("正确做法")).toBeInTheDocument();
    expect(screen.getByText("原题 / 情境")).toBeInTheDocument();
    expect(screen.getAllByText("含端点").length).toBeGreaterThan(0);
    expect(screen.getAllByText("不含端点").length).toBeGreaterThan(0);
    expect(screen.getByText(/交集结果/)).toBeInTheDocument();
    expect(screen.getAllByText(/课程要求/).length).toBeGreaterThan(0);
    // text 分段：两段各自成为独立段落
    expect(container.querySelectorAll(".lessonParagraph").length).toBe(2);
    // 正误对照三栏有独立标记，正确/错误栏可区分
    expect(container.querySelectorAll(".contrastCell").length).toBe(3);
    expect(container.querySelector(".contrastCellWrong")).not.toBeNull();
    expect(container.querySelector(".contrastCellRight")).not.toBeNull();
  });

  it("未知组件类型回退到安全文本，不渲染脚本或 HTML", () => {
    const bad = lesson([{ type: "raw_html", id: "x1", html: "<script>alert(1)</script>" }]);
    const result = validateLesson(bad);
    expect(result.ok).toBe(false);
    const { container } = render(<LessonRenderer lesson={bad} />);
    expect(screen.getByText("微课堂内容暂不可读")).toBeInTheDocument();
    expect(screen.getAllByText(/安全回退文本/).length).toBeGreaterThan(0);
    expect(container.querySelector("script")).toBeNull();
  });

  it("公式按受限表达式排版：上标、乘号、关系符不再当普通文本", () => {
    const math = lesson([
      { type: "formula", id: "f3", expr: "x^2*3=12", caption: "受限排版" },
    ]);
    const { container } = render(<LessonRenderer lesson={math} />);
    const formula = container.querySelector(".mathFormula");
    expect(formula).not.toBeNull();
    expect(formula?.getAttribute("aria-label")).toBe("公式 x^2*3=12");
    expect(formula?.querySelector("sup")?.textContent).toBe("2");
    expect(formula?.textContent).toBe("x2×3=12");
    expect(formula?.querySelector(".mathRelation")?.textContent).toBe("=");
    expect(screen.getByText(/不是完整 LaTeX 渲染/)).toBeInTheDocument();
  });

  it("公式内容不会被当作 HTML 注入", () => {
    const withMarkup = lesson([
      { type: "formula", id: "f2", expr: "<img src=x onerror=alert(1)>", caption: "危险表达式" },
    ]);
    const { container } = render(<LessonRenderer lesson={withMarkup} />);
    const formula = container.querySelector(".mathFormula");
    expect(formula?.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(formula?.getAttribute("aria-label")).toBe("公式 <img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("推导步骤逐行编号，编号与步骤文本对应", () => {
    const { container } = render(<LessonRenderer lesson={lesson(SIX_COMPONENTS)} />);
    const steps = container.querySelectorAll(".derivationStep");
    expect(steps.length).toBe(3);
    expect(within(steps[0] as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(steps[2] as HTMLElement).getByText("3")).toBeInTheDocument();
    expect(steps[1].textContent).toContain("两边减 8");
  });

  it("citation 可以展开已授权来源，并显示页码", () => {
    const withSource = lesson(
      [{ type: "citation", id: "z2", labels: [{ label: "课程要求", source_index: 0 }] }],
      { safe_sources: [{ source_label: "七年级数学教材", printed_page: 42 }] },
    );
    render(<LessonRenderer lesson={withSource} />);
    expect(screen.queryByText(/来源：/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /课程要求/ }));
    expect(screen.getByText(/来源：七年级数学教材 · 第 42 页/)).toBeInTheDocument();
    expect(screen.getByText(/不提供可点击的外部链接/)).toBeInTheDocument();
  });

  it("citation 没有来源原文时如实说明，不伪造链接", () => {
    const unresolved = lesson(
      [
        {
          type: "citation",
          id: "z3",
          labels: [{ label: "知识点「去括号的符号」", evidence_index: 0 }],
        },
      ],
      { safe_sources: [] },
    );
    const { container } = render(<LessonRenderer lesson={unresolved} />);
    fireEvent.click(screen.getByRole("button", { name: /知识点/ }));
    expect(screen.getByText(/没有附带可展开的证据原文/)).toBeInTheDocument();
    expect(container.querySelector("a")).toBeNull();
  });

  it("citation 必须恰有一个索引，否则拒绝渲染", () => {
    const noIndex = lesson([{ type: "citation", id: "z4", labels: [{ label: "没有索引" }] }]);
    expect(validateLesson(noIndex).ok).toBe(false);
    const both = lesson([
      { type: "citation", id: "z5", labels: [{ label: "两个索引", source_index: 0, evidence_index: 1 }] },
    ]);
    expect(validateLesson(both).ok).toBe(false);
  });

  it("数轴交集结构不符合审核规则时拒绝渲染", () => {
    const bad = lesson([
      {
        type: "number_line",
        id: "n2",
        min: "0",
        max: "10",
        tick: "1",
        sets: [
          { label: "A", intervals: [{ lo: "1", hi: "2", lo_closed: true, hi_closed: true }] },
          { label: "B", intervals: [{ lo: "2", hi: "3", lo_closed: true, hi_closed: true }] },
        ],
        operation: "intersection",
        result_index: 1,
      },
    ]);
    expect(validateLesson(bad).ok).toBe(false);
  });

  it("重复组件编号与超限刻度都不通过校验", () => {
    const duplicate = lesson([
      { type: "text", id: "same", paragraphs: ["第一段"] },
      { type: "text", id: "same", paragraphs: ["第二段"] },
    ]);
    expect(validateLesson(duplicate).ok).toBe(false);

    const tooManyTicks = lesson([
      {
        type: "number_line",
        id: "n3",
        min: "0",
        max: "100",
        tick: "1",
        sets: [{ label: "A", intervals: [{ lo: "1", hi: "2", lo_closed: true, hi_closed: false }] }],
        operation: null,
        result_index: null,
      },
    ]);
    expect(validateLesson(tooManyTicks).ok).toBe(false);
  });
});

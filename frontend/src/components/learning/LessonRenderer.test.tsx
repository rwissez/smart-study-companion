import { render, screen } from "@testing-library/react";
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
  { type: "text", id: "t1", heading: "规则", paragraphs: ["括号前是负号，去掉括号每一项都变号。"] },
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
    render(<LessonRenderer lesson={lesson(SIX_COMPONENTS)} />);
    expect(screen.getByText("括号前是负号，去掉括号每一项都变号。")).toBeInTheDocument();
    expect(screen.getByText("-(2x-7) = -2x+7")).toBeInTheDocument();
    expect(screen.getByText("等式变形")).toBeInTheDocument();
    expect(screen.getByText("错误做法")).toBeInTheDocument();
    expect(screen.getByText("正确做法")).toBeInTheDocument();
    expect(screen.getAllByText("含端点").length).toBeGreaterThan(0);
    expect(screen.getAllByText("不含端点").length).toBeGreaterThan(0);
    expect(screen.getByText(/交集结果/)).toBeInTheDocument();
    expect(screen.getAllByText(/课程要求/).length).toBeGreaterThan(0);
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

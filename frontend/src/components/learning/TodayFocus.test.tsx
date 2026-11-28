import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AlertItem, ClassResultView } from "../../api/types";
import { TodayFocus } from "./TodayFocus";

function makeResult(overrides: Partial<ClassResultView> = {}): ClassResultView {
  return {
    class_id: "DC01",
    node_id: "N01",
    analysis_version: "v1",
    run_id: "run-1",
    dataset_id: "ds-1",
    dataset_family_id: null,
    catalog_version: "c1",
    roster_count: 2,
    observed_student_count: 1,
    metrics: [],
    findings: [],
    common_findings: [],
    performance_distribution: [],
    attention_students: [],
    member_analysis_versions: [],
    summary: null,
    limitations: [],
    freshness: "current",
    ...overrides,
  };
}

const alert: AlertItem = {
  alert_id: "a1",
  kind: "plan_needs_review",
  status: "open",
  summary: "学习方案需要教师确认",
  target_ref: {},
  student_id: "S01",
  evidence_refs: [],
  created_at: "2026-11-28T10:00:00Z",
};

describe("TodayFocus 今日关注结论卡", () => {
  it("按 提醒→关注学生→共性问题 排序并给进入画像入口", () => {
    render(
      <MemoryRouter>
        <TodayFocus
          result={makeResult({
            attention_students: [
              { student_id: "S03", finding_ids: [], evidence_refs: [], reason: "没有充分证据判定表现", limitations: [] },
            ],
            common_findings: [
              {
                finding_id: "f1",
                indicator_id: "T01I1",
                description: "有效样题不足",
                evidence_status: "insufficient",
                performance: "undetermined",
                evidence_refs: [],
                limitations: [],
              },
            ],
          })}
          names={{ T01I1: "去括号时的符号处理" }}
          nodeLabel="第 1 次观察"
          alerts={[alert]}
          chatHref="/teacher/chat?class=DC01"
        />
      </MemoryRouter>
      );
    const list = screen.getByRole("list");
    expect(list).toHaveTextContent("学习方案需要教师确认");
    expect(list).toHaveTextContent("S03：没有充分证据判定表现");
    expect(list).toHaveTextContent("去括号时的符号处理");
    expect(screen.getAllByRole("link", { name: "进入画像" }).length).toBe(2);
    expect(screen.getByRole("link", { name: "就此班级提问 AI" })).toHaveAttribute("href", "/teacher/chat?class=DC01");
  });

  it("没有待处理事项时给出明确结论", () => {
    render(
      <MemoryRouter>
        <TodayFocus result={makeResult()} names={{}} nodeLabel={null} alerts={[]} chatHref="/teacher/chat" />
      </MemoryRouter>
    );
    expect(screen.getByText(/本轮没有需要立即处理的事项/)).toBeInTheDocument();
  });
});

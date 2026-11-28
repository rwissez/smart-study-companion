import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IndicatorRadar } from "./IndicatorRadar";
import type { Metric } from "../../api/types";

function metric(indicatorId: string, value: number | null, numerator: number | null, denominator: number | null): Metric {
  return {
    metric_id: `m-${indicatorId}`,
    name: "valid_score_ratio",
    value,
    numerator,
    denominator,
    unit: "ratio",
    scope: { student_id: "S01", class_id: null, node_id: "N01", indicator_id: indicatorId, topic_id: null, kp_id: null },
    evidence_refs: [],
    excluded_event_count: 0,
    limitations: [],
  };
}

const NAMES = { T01I1: "去括号的符号处理", T01I2: "解方程步骤", T02I1: "数轴解集" };

describe("IndicatorRadar", () => {
  it("存在缺失维度时不用 0 补齐，也不画闭合多边形", () => {
    const { container } = render(
      <IndicatorRadar
        metrics={[metric("T01I1", 1, 5, 5), metric("T01I2", 0.5, 5, 10), metric("T02I1", null, 0, 0)]}
        names={NAMES}
      />,
    );
    expect(container.querySelector("polygon")).toBeNull();
    expect(screen.getByText(/存在缺失指标/)).toBeInTheDocument();
    expect(screen.getAllByText("无可用结果").length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: /得分率雷达图/ })).toBeInTheDocument();
  });

  it("全部维度都有值时才画多边形，并给出分子分母数据表", () => {
    const { container } = render(
      <IndicatorRadar
        metrics={[metric("T01I1", 1, 5, 5), metric("T01I2", 0.5, 5, 10), metric("T02I1", 0.25, 1, 4)]}
        names={NAMES}
      />,
    );
    expect(container.querySelector("polygon")).not.toBeNull();
    expect(screen.getByText("分子 / 分母")).toBeInTheDocument();
    expect(screen.getByText("5 / 10")).toBeInTheDocument();
  });

  it("没有有效记录时保持空状态，不画 0 点", () => {
    render(<IndicatorRadar metrics={[metric("T01I1", null, null, null)]} names={NAMES} />);
    expect(screen.getByText("暂无有效记录，指标保持缺失。")).toBeInTheDocument();
  });
});

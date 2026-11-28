import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { DistributionBucket } from "../../api/types";
import { IndicatorDistribution } from "./IndicatorDistribution";

const names = { T01I1: "去括号时的符号处理", T01I2: "解方程步骤的完整性" };

const buckets: DistributionBucket[] = [
  { indicator_id: "T01I1", performance: "undetermined", evidence_status: "insufficient", count: 1, roster_count: 2 },
  { indicator_id: "T01I1", performance: "undetermined", evidence_status: "no_record", count: 1, roster_count: 2 },
  { indicator_id: "T01I2", performance: "weak", evidence_status: "sufficient", count: 1, roster_count: 2 },
];

describe("IndicatorDistribution 表现分布条", () => {
  it("按指标聚合并给出已观测人数与分段图例", () => {
    render(<IndicatorDistribution buckets={buckets} names={names} />);
    expect(screen.getByText("去括号时的符号处理")).toBeInTheDocument();
    expect(screen.getByText(/已观测 2 \/ 2/)).toBeInTheDocument();
    expect(screen.getByText(/未产生可用记录 1 人/)).toBeInTheDocument();
    const bar = screen.getByRole("img", { name: /去括号时的符号处理：已观测 2 人/ });
    expect(bar).toBeInTheDocument();
  });

  it("无数据时显示空态而不是空容器", () => {
    render(<IndicatorDistribution buckets={[]} names={names} />);
    expect(screen.getByText("服务端没有返回表现分布。")).toBeInTheDocument();
  });
});

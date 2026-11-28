import { describe, expect, it } from "vitest";
import {
  activityStateLabel,
  formatFraction,
  formatPercent,
  formatScore,
  gradingStatusLabel,
  progressLabel,
  questionTypeLabel,
} from "./format";

describe("format", () => {
  it("把比例转成百分比，缺失值不显示 0", () => {
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(1)).toBe("100.0%");
    expect(formatPercent(0.33333)).toBe("33.3%");
    expect(formatPercent(null)).toBe("无可用结果");
    expect(formatPercent(undefined)).toBe("无可用结果");
  });

  it("得分缺失显示破折号，不伪造 0 分", () => {
    expect(formatScore(null, 5)).toBe("—");
    expect(formatScore(3, 5)).toBe("3 / 5");
    expect(formatScore(0, 5)).toBe("0 / 5");
  });

  it("分子分母缺失时保留破折号", () => {
    expect(formatFraction(2, 3)).toBe("2 / 3");
    expect(formatFraction(null, null)).toBe("— / —");
  });

  it("状态与分类文案映射明确", () => {
    expect(progressLabel("partially_improved")).toBe("有所改善");
    expect(progressLabel("insufficient")).toBe("证据不足");
    expect(progressLabel("unknown_value")).toBe("证据不足");
    expect(activityStateLabel("cancelled")).toBe("已取消");
    expect(gradingStatusLabel("needs_review")).toBe("待复核");
    expect(questionTypeLabel("fill_blank")).toBe("填空题");
  });
});

import type {
  EvidenceRef,
  EvidenceStatus,
  Finding,
  GenerationMethod,
  GradingStatus,
  Performance,
  ReviewStatus,
} from "../api/types";

const metricNames = new Map([
  ["valid_score_ratio", "得分率"],
  ["full_score_ratio", "满分作答比例"],
  ["observed_response_ratio", "已有记录作答比例"],
  ["item_coverage", "题目覆盖率"],
  ["student_observation_rate", "班级学生记录覆盖率"],
]);

export function formatMetricName(name: string): string {
  return metricNames.get(name) ?? (/[\u3400-\u9fff]/.test(name) ? name : "统计指标");
}

const evidenceNames: Record<EvidenceRef["kind"], string> = {
  record: "作答记录",
  metric: "统计依据",
  material: "材料",
  curriculum: "课程条目",
};

export function formatEvidenceLabel(ref: Pick<EvidenceRef, "id" | "kind">, position?: number): string {
  // 分析服务把指标字段名放在统计引用编号的最后一段。
  const metricName = ref.kind === "metric" ? metricNames.get(ref.id.split("-").at(-1) ?? "") : undefined;
  return metricName ?? `${evidenceNames[ref.kind]}${position === undefined ? "" : ` ${position}`}`;
}

export function formatRuleVersion(version: string): string {
  return version.replace(/^rules-(\d+)$/, "第 $1 版");
}

export function formatAnalysisLimitation(text: string): string {
  return text.replace(/^表现判定阈值(\d+(?:\.\d+)?)，规则版本(rules-\d+)$/, (_, threshold, version) =>
    `表现判定线 ${formatRatio(Number(threshold))}（规则${formatRuleVersion(version)}）`,
  );
}

/** 比例统一格式化：服务返回 0—1，前端只做显示转换 */
export function formatRatio(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "无可用结果";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatNumber(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "无可用结果";
  return String(value);
}

export function formatFraction(numerator: number | null, denominator: number | null): string {
  if (numerator === null || denominator === null || denominator === 0) {
    return "无可用结果";
  }
  return `${numerator} / ${denominator}`;
}

export const performanceLabel: Record<Performance, string> = {
  observed_strength: "表现较好",
  needs_practice: "需要练习",
  undetermined: "暂不能确定",
};

export const evidenceStatusLabel: Record<EvidenceStatus, string> = {
  sufficient: "证据充分",
  insufficient: "证据不足",
  conflicting: "记录冲突",
};

export function formatFindingTitle(finding: Finding, indicatorName?: string): string {
  const name = indicatorName ?? "学习要求";
  if (finding.evidence_status === "conflicting") return `需复核 · ${name}`;
  if (finding.evidence_status === "insufficient" || finding.performance === "undetermined") {
    return `暂待观察 · ${name}`;
  }
  const conclusion = finding.performance === "needs_practice" ? "需要巩固" : "本次表现较好";
  return `${conclusion} · ${name}`;
}

export const gradingStatusLabel: Record<GradingStatus, string> = {
  已评分: "已评分",
  待评分: "待评分",
  未作答: "未作答",
};

export const reviewStatusLabel: Record<ReviewStatus, string> = {
  draft: "草稿",
  validated: "已校验",
  needs_review: "待复核",
};

export const generationMethodLabel: Record<GenerationMethod, string> = {
  rule: "规则生成",
  model: "模型生成",
};

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

export function percentOrDash(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

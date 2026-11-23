import type {
  EvidenceStatus,
  GenerationMethod,
  GradingStatus,
  Performance,
  ReviewStatus,
} from "../api/types";

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

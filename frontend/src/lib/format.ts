import type { ProgressClassification } from "../api/types";

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDay(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/** 比例统一按 0—100 展示；无记录返回 null，由调用方显示“无可用结果”。 */
export function ratioPercent(value: number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  return Math.round(value * 1000) / 10;
}

export function formatPercent(value: number | null | undefined): string {
  const percent = ratioPercent(value);
  return percent === null ? "无可用结果" : `${percent.toFixed(1)}%`;
}

export function formatScore(score: number | null | undefined, max: number | null | undefined): string {
  if (score === null || score === undefined) return "—";
  return max === null || max === undefined ? String(score) : `${score} / ${max}`;
}

export function formatFraction(numerator: number | null | undefined, denominator: number | null | undefined): string {
  return `${numerator ?? "—"} / ${denominator ?? "—"}`;
}

const EVIDENCE_STATUS_LABELS: Record<string, string> = {
  sufficient: "证据充分",
  insufficient: "证据不足",
  conflicting: "证据冲突",
};

export function evidenceStatusLabel(status: string | null | undefined): string {
  if (!status) return "证据状态未知";
  return EVIDENCE_STATUS_LABELS[status] ?? `证据状态：${status}`;
}

const PERFORMANCE_LABELS: Record<string, string> = {
  observed_strength: "观察到的优势",
  needs_practice: "需要练习",
  undetermined: "暂不定论",
};

export function performanceLabel(performance: string | null | undefined): string {
  if (!performance) return "暂不定论";
  return PERFORMANCE_LABELS[performance] ?? performance;
}

const PROGRESS_LABELS: Record<ProgressClassification, string> = {
  improved: "明显改善",
  partially_improved: "有所改善",
  unchanged_or_declined: "无明显改善或退步",
  insufficient: "证据不足",
};

export function progressLabel(classification: string | null | undefined): string {
  if (!classification) return "证据不足";
  return PROGRESS_LABELS[classification as ProgressClassification] ?? "证据不足";
}

const ACTIVITY_STATE_LABELS: Record<string, string> = {
  not_started: "未开始",
  in_progress: "进行中",
  completed: "已完成",
  cancelled: "已取消",
};

export function activityStateLabel(state: string | null | undefined): string {
  if (!state) return "状态未知";
  return ACTIVITY_STATE_LABELS[state] ?? state;
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function jobStatusLabel(status: string | null | undefined): string {
  if (!status) return "状态未知";
  return JOB_STATUS_LABELS[status] ?? status;
}

const ACTIVITY_KIND_LABELS: Record<string, string> = {
  question: "题目",
  study_material: "学习材料",
  micro_lesson: "微课堂",
};

export function activityKindLabel(kind: string | null | undefined): string {
  if (!kind) return "活动";
  return ACTIVITY_KIND_LABELS[kind] ?? kind;
}

const QUESTION_TYPE_LABELS: Record<string, string> = {
  choice: "选择题",
  fill_blank: "填空题",
  solution: "解答题",
};

export function questionTypeLabel(type: string | null | undefined): string {
  if (!type) return "题目";
  return QUESTION_TYPE_LABELS[type] ?? type;
}

const GRADING_LABELS: Record<string, string> = {
  graded: "已评分",
  pending: "待评分",
  needs_review: "待复核",
  failed: "评分失败",
  submitted: "已提交",
};

export function gradingStatusLabel(status: string | null | undefined): string {
  if (!status) return "状态未知";
  return GRADING_LABELS[status] ?? status;
}

export function truncate(value: string, max = 48): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`;
}

import type { ClassResult, EvidenceRecord, RecommendationBundle, StudentResult } from "../../types";
import { demoClassResults } from "./classes";
import { demoDataset, demoRun } from "./dataset";
import { demoRecommendations } from "./recommendations";
import { demoStudentResults } from "./students";

export const observedDataset = { ...demoDataset, dataset_id: "DEMO-DATA-OBSERVED", record_count: 8 };
export const observedRun = {
  ...demoRun,
  dataset_id: observedDataset.dataset_id,
  run_id: "DEMO-RUN-OBSERVED",
  node_ids: ["DEMO-N1"],
};

export const observedRecord: EvidenceRecord = {
  record_id: "DEMO-R-OBSERVED",
  student_id: "DEMO-S3",
  material_id: "DEMO-M2",
  node_id: "DEMO-N1",
  indicator_id: "DEMO-I1",
  grading_status: "已评分",
  score: 2,
  max_score: 3,
  final_answer: "x = 6",
  solution_steps: ["3x − x = 7 + 5", "2x = 12", "x = 6"],
  rubric_scores: [
    { point_id: "DEMO-M2P1", score: 1 }, { point_id: "DEMO-M2P2", score: 1 },
    { point_id: "DEMO-M2P3", score: 0 },
  ],
  submitted_at: "2026-11-21T09:00:00+08:00",
  disposition: "selected",
  disposition_note: null,
};
const observedRef = { kind: "record" as const, id: observedRecord.record_id, run_id: observedRun.run_id };

function withObservationScope<T extends StudentResult | ClassResult>(result: T): T {
  const rebind = <Item extends { evidence_refs: Array<{ run_id?: string | null }> }>(item: Item): Item =>
    ({ ...item, evidence_refs: item.evidence_refs.map((ref) =>
      ref.run_id ? { ...ref, run_id: observedRun.run_id } : ref) });
  return {
    ...result,
    dataset_id: observedRun.dataset_id,
    run_id: observedRun.run_id,
    metrics: result.metrics.map(rebind),
    findings: result.findings.map(rebind),
  };
}

export function observedStudentResult(studentId: string, nodeId: string) {
  const original = demoStudentResults[studentId]?.[nodeId];
  if (nodeId !== "DEMO-N1" || !original) return undefined;
  const result = withObservationScope(original);
  if (studentId !== "DEMO-S3") return result;
  result.metrics = [{
    metric_id: "DEMO-S3-OBSERVED", name: "得分率", value: 2 / 3, unit: "ratio",
    numerator: 2, denominator: 3, scope: { student_id: studentId, node_id: nodeId, indicator_id: "DEMO-I1" },
    evidence_refs: [observedRef], excluded_event_count: 0, limitations: ["仅有一条新增示例记录"],
  }];
  result.findings = [{
    ...result.findings[0],
    description: "新增记录中正确求出方程的解，未写代入检验。单题证据不足以判断是否能稳定验算。",
    evidence_refs: [observedRef],
    limitations: ["仅覆盖一条新增示例记录"],
  }];
  result.limitations = ["补充观察示例，不能据此判断教学成效"];
  return result;
}

export function observedClassResult(classId: string, nodeId: string) {
  const original = demoClassResults[classId]?.[nodeId];
  if (nodeId !== "DEMO-N1" || !original) return undefined;
  const result = withObservationScope(original);
  if (classId !== "DEMO-B") return result;
  result.observed_student_count = 2;
  result.metrics = [{
    ...result.metrics[0], value: 1 / 3, numerator: 2, denominator: 6,
    evidence_refs: [...result.metrics[0].evidence_refs, observedRef],
    limitations: ["两名学生各有一条有效示例作答"],
  }];
  result.findings = [];
  return result;
}

export function observedRecommendations(studentId: string, nodeId: string): RecommendationBundle | undefined {
  const original = demoRecommendations[studentId]?.[nodeId];
  if (!original || nodeId !== "DEMO-N1") return undefined;
  const bundle = { ...original, dataset_id: observedDataset.dataset_id, run_id: observedRun.run_id };
  if (studentId !== "DEMO-S3") return bundle;
  const first = bundle.recommendations[0];
  return { ...bundle, recommendations: [{
    ...first, goal: "补写代入检验", reason: "新增作答求出了方程的解。下一步观察是否会检验。",
    activities: [{
      ...first.activities[1], order: 1, previously_attempted: true,
      purpose: "在已做过的题目中补写验算过程",
      hint_levels: ["把求出的数分别代入等号左右两边。"],
      observation_points: ["能否说明等号两边是否相等"],
    }],
    adjustment_conditions: [{
      observable_condition: "能正确代入并比较等号两边",
      next_action: "换一题继续观察能否独立检验",
    }],
  }] };
}

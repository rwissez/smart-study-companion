import type {
  Catalog,
  ClassResult,
  ClassSummary,
  DatasetSummary,
  EvidenceRecord,
  Job,
  Material,
  Metric,
  Page,
  QualityIssue,
  Recommendation,
  RecommendationBundle,
  RosterStudent,
  RunSummary,
  SessionInfo,
  StudentResult,
} from "../types";
import { demoCatalog } from "./fixtures/catalog";
import {
  demoClassNames,
  demoClasses,
  demoDataset,
  demoNodes,
  demoRoster,
  demoRun,
} from "./fixtures/dataset";
import { demoClassResults } from "./fixtures/classes";
import { demoStudentResults } from "./fixtures/students";
import { demoMaterials } from "./fixtures/materials";
import { demoRecommendations } from "./fixtures/recommendations";
import { demoExportManifest, demoQualityIssues, demoRecords } from "./fixtures/evidence";

import { observedClassResult, observedDataset, observedRecommendations, observedRecord, observedRun, observedStudentResult } from "./fixtures/observation";

const knownDataset = (id: string) => id === demoDataset.dataset_id || id === observedDataset.dataset_id;
const knownRun = (id: string) => id === demoRun.run_id || id === observedRun.run_id;

export class DemoError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status = 400) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/** 统一加一点延迟，让加载态在界面上真实可见 */
function delay<T>(value: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export const demoRuntime = {
  datasetId: demoDataset.dataset_id,
  runId: demoRun.run_id,
  catalogVersion: demoCatalog.catalog_version,
  nodes: demoNodes,
  classNames: demoClassNames,
};

export async function listIdentities(): Promise<{
  dataset: DatasetSummary;
  classes: ClassSummary[];
  students: RosterStudent[];
}> {
  return delay({
    dataset: demoDataset,
    classes: demoClasses,
    students: demoRoster,
  });
}

export async function createSession(
  role: "teacher" | "student",
  studentId?: string,
): Promise<SessionInfo> {
  if (role === "student" && !studentId) {
    throw new DemoError("INVALID_STUDENT", "学生身份必须选择本人。", 422);
  }
  if (role === "student" && !demoRoster.some((s) => s.student_id === studentId)) {
    throw new DemoError("STUDENT_NOT_FOUND", "该学生不在示例数据集里。", 404);
  }
  return delay({
    session_id: `demo-session-${role}-${studentId ?? "teacher"}`,
    session_token: `demo-token-${role}`,
    role,
    scope: {
      dataset_id: role === "student" ? demoDataset.dataset_id : null,
      student_id: role === "student" ? (studentId ?? null) : null,
    },
  });
}

export async function listDatasets(): Promise<DatasetSummary[]> {
  return delay([demoDataset, observedDataset]);
}

export async function listRuns(datasetId: string): Promise<RunSummary[]> {
  if (!knownDataset(datasetId)) {
    throw new DemoError("DATASET_NOT_FOUND", "数据集不存在。", 404);
  }
  return delay([datasetId === observedDataset.dataset_id ? observedRun : demoRun]);
}

export async function getNodes(
  runId: string,
): Promise<Array<{ node_id: string; name: string; teaching_scope: string }>> {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  return delay(runId === observedRun.run_id ? demoNodes.slice(0, 1) : demoNodes);
}

export async function listClasses(datasetId: string): Promise<ClassSummary[]> {
  if (!knownDataset(datasetId)) {
    throw new DemoError("DATASET_NOT_FOUND", "数据集不存在。", 404);
  }
  return delay(demoClasses);
}

export async function listRoster(
  datasetId: string,
  classId: string | null,
  page = 1,
  pageSize = 50,
): Promise<Page<RosterStudent>> {
  if (!knownDataset(datasetId)) throw new DemoError("DATASET_NOT_FOUND", "数据集不存在。", 404);
  const all = demoRoster.filter((s) => !classId || s.class_id === classId).map((student) =>
    datasetId === observedDataset.dataset_id && student.student_id === "DEMO-S3"
      ? { ...student, observed_event_count: 1 } : student);
  const start = (page - 1) * pageSize;
  return delay({
    items: all.slice(start, start + pageSize),
    total: all.length,
    page,
    page_size: pageSize,
  });
}

export async function getClassResult(
  runId: string,
  classId: string,
  nodeId: string,
): Promise<ClassResult> {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  const result = runId === observedRun.run_id ? observedClassResult(classId, nodeId) : demoClassResults[classId]?.[nodeId];
  if (!result) {
    throw new DemoError("RESULT_NOT_FOUND", "该班级在所选节点没有结果。", 404);
  }
  return delay(result);
}

export async function getStudentResult(
  runId: string,
  studentId: string,
  nodeId: string,
): Promise<StudentResult> {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  const result = runId === observedRun.run_id ? observedStudentResult(studentId, nodeId) : demoStudentResults[studentId]?.[nodeId];
  if (!result) {
    throw new DemoError("RESULT_NOT_FOUND", "该学生在此节点没有结果。", 404);
  }
  return delay(result);
}

export async function getCatalog(): Promise<Catalog> {
  return delay(demoCatalog);
}

export async function getMaterial(materialId: string, role?: "teacher" | "student"): Promise<Material> {
  const material = demoMaterials[materialId];
  if (!material) {
    throw new DemoError("MATERIAL_NOT_FOUND", "材料不存在或未在当前目录版本中。", 404);
  }
  if (role === "teacher") return delay(material);
  const { reference_answer, reference_explanation, rubric_points, ...studentMaterial } = material;
  return delay(studentMaterial);
}

const editedRecommendations = new Map<string, RecommendationBundle>();
const revisionHistory = new Map<string, Array<{ recommendation: Recommendation; reason: string }>>();

export function getRevisionHistory(bundle: RecommendationBundle, id: string) {
  return revisionHistory.get(revisionKey(bundle, id)) ?? [];
}

function revisionKey(bundle: RecommendationBundle, id: string) {
  return JSON.stringify([bundle.run_id, bundle.student_id, bundle.node_id, id]);
}

function recommendationKey(runId: string, studentId: string, nodeId: string) {
  return JSON.stringify([runId, studentId, nodeId]);
}

export function saveRecommendation(bundle: RecommendationBundle, recommendation: Recommendation, reason: string) {
  const key = revisionKey(bundle, recommendation.recommendation_id);
  const previous = bundle.recommendations.find((item) => item.recommendation_id === recommendation.recommendation_id);
  if (previous) revisionHistory.set(key, [...getRevisionHistory(bundle, recommendation.recommendation_id), { recommendation: previous, reason }]);
  const updated = { ...bundle, recommendations: bundle.recommendations.map((item) =>
    item.recommendation_id === recommendation.recommendation_id ? recommendation : item) };
  editedRecommendations.set(recommendationKey(bundle.run_id, bundle.student_id, bundle.node_id), updated);
  return updated;
}

export async function getRecommendations(
  runId: string,
  studentId: string,
  nodeId: string,
): Promise<RecommendationBundle> {
  if (!knownRun(runId)) throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  const bundle = editedRecommendations.get(recommendationKey(runId, studentId, nodeId))
    ?? (runId === observedRun.run_id ? observedRecommendations(studentId, nodeId) : demoRecommendations[studentId]?.[nodeId]);
  if (!bundle) {
    throw new DemoError("RECOMMENDATION_NOT_FOUND", "该学生在此节点暂无建议。", 404);
  }
  return delay(bundle);
}

export async function readRecord(runId: string, recordId: string): Promise<EvidenceRecord> {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  const record = runId === observedRun.run_id && recordId === observedRecord.record_id
    ? observedRecord : demoRecords[recordId];
  if (runId === observedRun.run_id && record?.node_id !== "DEMO-N1") {
    throw new DemoError("RECORD_NOT_FOUND", "记录不在该运行的测验范围内。", 404);
  }
  if (!record) {
    throw new DemoError("RECORD_NOT_FOUND", "记录不存在。", 404);
  }
  return delay(record);
}

export async function readMetric(runId: string, metricId: string): Promise<Metric> {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  if (runId === observedRun.run_id) {
    const results = [
      ...demoRoster.map((student) => observedStudentResult(student.student_id, "DEMO-N1")),
      ...demoClasses.map((item) => observedClassResult(item.class_id, "DEMO-N1")),
    ];
    const metric = results.flatMap((result) => result?.metrics ?? []).find((item) => item.metric_id === metricId);
    if (!metric) throw new DemoError("METRIC_NOT_FOUND", "统计项不存在。", 404);
    return delay(metric);
  }
  for (const byNode of Object.values(demoStudentResults)) {
    for (const result of Object.values(byNode)) {
      const found = result.metrics.find((m) => m.metric_id === metricId);
      if (found) return delay(found);
    }
  }
  for (const byNode of Object.values(demoClassResults)) {
    for (const result of Object.values(byNode)) {
      const found = result.metrics.find((m) => m.metric_id === metricId);
      if (found) return delay(found);
    }
  }
  throw new DemoError("METRIC_NOT_FOUND", "统计项不存在。", 404);
}

export async function listQuality(datasetId: string): Promise<QualityIssue[]> {
  if (!knownDataset(datasetId)) {
    throw new DemoError("DATASET_NOT_FOUND", "数据集不存在。", 404);
  }
  return delay(demoQualityIssues);
}

export async function getExportPreview(runId: string) {
  if (!knownRun(runId)) {
    throw new DemoError("RUN_NOT_FOUND", "运行不存在。", 404);
  }
  return delay(runId === observedRun.run_id
    ? { ...demoExportManifest, run_id: observedRun.run_id, dataset_id: observedDataset.dataset_id }
    : demoExportManifest);
}

/** 示例模式下的运行模拟：只演示状态变化，不产生真实分析结果 */
export async function simulateRun(datasetId: string): Promise<Job<void>> {
  if (!knownDataset(datasetId)) {
    throw new DemoError("DATASET_NOT_FOUND", "数据集不存在。", 404);
  }
  await delay(null, 400);
  return {
    job_id: "DEMO-JOB-1",
    status: "succeeded",
    result: null,
    error: null,
  };
}

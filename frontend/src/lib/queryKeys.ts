import type { DataSource } from "../api/service";

/** 查询键必须包含数据来源、会话代次、数据集、对象类型、对象、节点、版本与分页。 */
export interface ScopeKey {
  mode: DataSource;
  epoch: number;
  datasetId: string | null;
}

export function scopeKey(mode: DataSource, epoch: number, datasetId: string | null): ScopeKey {
  return { mode, epoch, datasetId };
}

const base = (scope: ScopeKey) => [scope.mode, scope.epoch, scope.datasetId] as const;

export const queryKeys = {
  health: (scope: ScopeKey) => [scope.mode, scope.epoch, "health"] as const,
  datasets: (scope: ScopeKey) => [...base(scope), "dataset", "list"] as const,
  roster: (scope: ScopeKey, classId: string | null) => [...base(scope), "roster", "list", classId ?? "all"] as const,
  classes: (scope: ScopeKey) => [...base(scope), "class", "list"] as const,
  runs: (scope: ScopeKey) => [...base(scope), "run", "list"] as const,
  classProfile: (scope: ScopeKey, classId: string, nodeId: string | null, analysisVersion: string | null) =>
    [...base(scope), "class", "profile", classId, "node", nodeId ?? "default", "version", analysisVersion ?? "current"] as const,
  alerts: (scope: ScopeKey) => [...base(scope), "alert", "list"] as const,
  comparison: (scope: ScopeKey, runId: string, nodeId: string, leftClassId: string, rightClassId: string) =>
    [...base(scope), "class", "compare", runId, "node", nodeId, "left", leftClassId, "right", rightClassId] as const,
  studentProfile: (scope: ScopeKey, studentId: string, nodeId: string | null, analysisVersion: string | null) =>
    [...base(scope), "student", "profile", studentId, "node", nodeId ?? "default", "version", analysisVersion ?? "current"] as const,
  plan: (scope: ScopeKey, studentId: string, nodeId: string | null, planVersion: string | null) =>
    [...base(scope), "student", "plan", studentId, "node", nodeId ?? "default", "version", planVersion ?? "current"] as const,
  progress: (scope: ScopeKey, studentId: string, page: number) =>
    [...base(scope), "student", "progress", studentId, "page", page] as const,
  answerRecords: (scope: ScopeKey, runId: string, studentId: string, nodeId: string, page: number) =>
    [...base(scope), "student", "records", studentId, "run", runId, "node", nodeId, "page", page] as const,
  learningEvents: (scope: ScopeKey, studentId: string) =>
    [...base(scope), "student", "events", studentId] as const,
  catalog: (scope: ScopeKey, catalogVersion: string | null) =>
    [...base(scope), "catalog", "version", catalogVersion ?? "current"] as const,
  knowledge: (scope: ScopeKey, knowledgeVersion: string | null) =>
    [...base(scope), "knowledge", "version", knowledgeVersion ?? "current"] as const,
  knowledgeReviews: (scope: ScopeKey) => [...base(scope), "knowledge", "reviews"] as const,
  knowledgeQuality: (scope: ScopeKey) => [...base(scope), "knowledge", "quality"] as const,
  quality: (scope: ScopeKey) => [...base(scope), "quality", "dataset"] as const,
  analysisQuality: (scope: ScopeKey, runId: string, nodeId: string, classId: string | null) =>
    [...base(scope), "quality", "analysis", runId, "node", nodeId, "class", classId ?? "all"] as const,
  jobs: (scope: ScopeKey) => [...base(scope), "job", "list"] as const,
  job: (scope: ScopeKey, jobId: string) => [...base(scope), "job", jobId] as const,
  jobEvents: (scope: ScopeKey, jobId: string) => [...base(scope), "job", jobId, "events"] as const,
  material: (scope: ScopeKey, materialId: string, catalogVersion: string | null, planVersion: string | null, activityId: string | null) =>
    [...base(scope), "material", materialId, "catalog", catalogVersion ?? "current", "plan", planVersion ?? "default", "activity", activityId ?? "none"] as const,
  attempt: (scope: ScopeKey, attemptId: string) => [...base(scope), "attempt", attemptId] as const,
  modelSettings: (scope: ScopeKey) => [...base(scope), "model", "settings"] as const,
  conversations: (scope: ScopeKey) => [...base(scope), "chat", "conversations"] as const,
  conversationMessages: (scope: ScopeKey, conversationId: string) =>
    [...base(scope), "chat", "messages", conversationId] as const,
  exports: (scope: ScopeKey, runId: string) => [...base(scope), "export", runId] as const,
  exportPreflight: (scope: ScopeKey, runId: string, nodeIds: string, caseIds: string) =>
    [...base(scope), "export", runId, "preflight", nodeIds, caseIds] as const,
  exportItem: (scope: ScopeKey, exportId: string) => [...base(scope), "export", "item", exportId] as const,
  revisions: (scope: ScopeKey, runId: string, studentId: string, nodeId: string) =>
    [...base(scope), "plan", "revisions", runId, studentId, "node", nodeId] as const,
  intervention: (scope: ScopeKey, runId: string, studentId: string, caseId: string, nodeId: string) =>
    [...base(scope), "intervention", runId, studentId, caseId, "node", nodeId] as const,
  recommendationsQuality: (scope: ScopeKey, runId: string, nodeId: string, classId: string | null) =>
    [...base(scope), "quality", "recommendations", runId, "node", nodeId, "class", classId ?? "all"] as const,
  materials: (scope: ScopeKey, catalogVersion: string | null) =>
    [...base(scope), "material", "search", "catalog", catalogVersion ?? "current"] as const,
};

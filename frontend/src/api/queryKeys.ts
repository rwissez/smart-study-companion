import type { SessionInfo } from "./types";

/**
 * 查询键：至少包含数据源、会话、数据集、运行、节点、资源与对象，
 * 切换范围时旧缓存不参与展示（对应 docs/product/frontend_architecture.md 的规则）。
 */
export const queryKeys = {
  all: ["ssc"] as const,
  identities: () => [...queryKeys.all, "identities"] as const,
  datasets: (session: SessionInfo | null) =>
    [...queryKeys.all, session?.session_id ?? "anon", "datasets"] as const,
  runs: (session: SessionInfo | null, datasetId: string | null) =>
    [...queryKeys.all, session?.session_id ?? "anon", "runs", datasetId] as const,
  classes: (session: SessionInfo | null, datasetId: string | null) =>
    [...queryKeys.all, session?.session_id ?? "anon", "classes", datasetId] as const,
  roster: (
    session: SessionInfo | null,
    datasetId: string | null,
    classId: string | null,
    page: number,
  ) =>
    [
      ...queryKeys.all,
      session?.session_id ?? "anon",
      "roster",
      datasetId,
      classId,
      page,
    ] as const,
  classResult: (
    session: SessionInfo | null,
    runId: string | null,
    classId: string | null,
    nodeId: string | null,
  ) =>
    [
      ...queryKeys.all,
      session?.session_id ?? "anon",
      "classResult",
      runId,
      classId,
      nodeId,
    ] as const,
  studentResult: (
    session: SessionInfo | null,
    runId: string | null,
    studentId: string | null,
    nodeId: string | null,
  ) =>
    [
      ...queryKeys.all,
      session?.session_id ?? "anon",
      "studentResult",
      runId,
      studentId,
      nodeId,
    ] as const,
  catalog: (catalogVersion: string | null) =>
    [...queryKeys.all, "catalog", catalogVersion] as const,
  material: (catalogVersion: string | null, materialId: string) =>
    [...queryKeys.all, "material", catalogVersion, materialId] as const,
  recommendations: (
    session: SessionInfo | null,
    runId: string | null,
    studentId: string | null,
    nodeId: string | null,
  ) =>
    [
      ...queryKeys.all,
      session?.session_id ?? "anon",
      "recommendations",
      runId,
      studentId,
      nodeId,
    ] as const,
  record: (session: SessionInfo | null, runId: string | null, recordId: string) =>
    [...queryKeys.all, session?.session_id ?? "anon", "record", runId, recordId] as const,
  metric: (session: SessionInfo | null, runId: string | null, metricId: string) =>
    [...queryKeys.all, session?.session_id ?? "anon", "metric", runId, metricId] as const,
  quality: (session: SessionInfo | null, datasetId: string | null) =>
    [...queryKeys.all, session?.session_id ?? "anon", "quality", datasetId] as const,
  exportPreview: (session: SessionInfo | null, runId: string | null) =>
    [...queryKeys.all, session?.session_id ?? "anon", "export", runId] as const,
};

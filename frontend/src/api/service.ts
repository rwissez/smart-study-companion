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
} from "./types";
import { request } from "./client";
import * as demo from "./demo/demoService";

/**
 * 数据来源：demo 用浏览器内的固定示例，api 走 A 的 HTTP 服务。
 * 两者不互相回退；配置值无法识别时显示配置错误（见 docs/product/frontend_architecture.md）。
 */
export type DataSource = "demo" | "api" | "invalid";

export const dataSource: DataSource = (() => {
  const raw = import.meta.env.VITE_DATA_SOURCE as string | undefined;
  if (!raw) return "demo";
  if (raw === "demo" || raw === "api") return raw;
  return "invalid";
})();

export const isDemo = dataSource === "demo";

export interface IdentityOptions {
  dataset: DatasetSummary;
  classes: ClassSummary[];
  students: RosterStudent[];
}

export async function listIdentities(): Promise<IdentityOptions> {
  if (isDemo) return demo.listIdentities();
  const datasets = await request<DatasetSummary[]>("/datasets");
  const first = datasets[0];
  if (!first) throw new Error("服务里还没有可用的数据集。");
  const [classes, roster] = await Promise.all([
    request<ClassSummary[]>(`/datasets/${first.dataset_id}/classes`),
    request<Page<RosterStudent>>(
      `/datasets/${first.dataset_id}/students?page=1&page_size=200`,
    ),
  ]);
  return { dataset: first, classes, students: roster.items };
}

export async function createSession(
  role: "teacher" | "student",
  studentId?: string,
  datasetId?: string,
): Promise<SessionInfo> {
  if (isDemo) return demo.createSession(role, studentId);
  return request<SessionInfo>("/demo-sessions", {
    method: "POST",
    body: JSON.stringify({
      role,
      dataset_id: datasetId ?? null,
      student_id: role === "student" ? studentId : null,
    }),
  });
}

export async function listDatasets(): Promise<DatasetSummary[]> {
  if (isDemo) return demo.listDatasets();
  return request<DatasetSummary[]>("/datasets");
}

export async function listRuns(datasetId: string): Promise<RunSummary[]> {
  if (isDemo) return demo.listRuns(datasetId);
  return request<RunSummary[]>(`/datasets/${datasetId}/runs`);
}

export interface NodeOption {
  node_id: string;
  name: string;
  teaching_scope: string;
}

export async function getNodes(runId: string): Promise<NodeOption[]> {
  if (isDemo) return demo.getNodes(runId);
  const run = await request<RunSummary>(`/runs/${runId}`);
  // 节点显示名称的来源仍是 Q27，联调前直接用编号，不自行编造名称
  return run.node_ids.map((id) => ({
    node_id: id,
    name: id,
    teaching_scope: "节点名称待联调确认",
  }));
}

export async function listClasses(datasetId: string): Promise<ClassSummary[]> {
  if (isDemo) return demo.listClasses(datasetId);
  return request<ClassSummary[]>(`/datasets/${datasetId}/classes`);
}

export async function listRoster(
  datasetId: string,
  classId: string | null,
  page = 1,
): Promise<Page<RosterStudent>> {
  if (isDemo) return demo.listRoster(datasetId, classId, page);
  const query = new URLSearchParams({ page: String(page), page_size: "50" });
  if (classId) query.set("class_id", classId);
  return request<Page<RosterStudent>>(
    `/datasets/${datasetId}/students?${query.toString()}`,
  );
}

export async function getClassResult(
  runId: string,
  classId: string,
  nodeId: string,
): Promise<ClassResult> {
  if (isDemo) return demo.getClassResult(runId, classId, nodeId);
  return request<ClassResult>(
    `/runs/${runId}/classes/${classId}?node_id=${encodeURIComponent(nodeId)}`,
  );
}

export async function getStudentResult(
  runId: string,
  studentId: string,
  nodeId: string,
): Promise<StudentResult> {
  if (isDemo) return demo.getStudentResult(runId, studentId, nodeId);
  return request<StudentResult>(
    `/runs/${runId}/students/${studentId}?node_id=${encodeURIComponent(nodeId)}`,
  );
}

export async function getCatalog(catalogVersion?: string | null): Promise<Catalog> {
  if (isDemo) return demo.getCatalog();
  return request<Catalog>(
    catalogVersion ? `/catalog?catalog_version=${catalogVersion}` : "/catalog",
  );
}

export async function getMaterial(
  materialId: string,
  catalogVersion?: string | null,
  role?: "teacher" | "student",
): Promise<Material> {
  if (isDemo) return demo.getMaterial(materialId, role);
  return request<Material>(
    catalogVersion
      ? `/materials/${materialId}?catalog_version=${catalogVersion}`
      : `/materials/${materialId}`,
  );
}

export function demoRecommendationHistory(bundle: RecommendationBundle, id: string) {
  return demo.getRevisionHistory(bundle, id);
}

export function demoSaveRecommendation(bundle: RecommendationBundle, recommendation: Recommendation, reason: string) {
  return demo.saveRecommendation(bundle, recommendation, reason);
}

export async function getRecommendations(
  runId: string,
  studentId: string,
  nodeId: string,
): Promise<RecommendationBundle> {
  if (isDemo) return demo.getRecommendations(runId, studentId, nodeId);
  // 生效建议的读取路径仍是 Q10／Q15，联调前明确报错，不猜接口
  throw new Error("生效建议的读取接口待 A 确认（Q10／Q15）。");
}

export async function readEvidence(
  runId: string,
  kind: "record" | "metric",
  id: string,
): Promise<EvidenceRecord | Metric> {
  if (isDemo) {
    return kind === "record" ? demo.readRecord(runId, id) : demo.readMetric(runId, id);
  }
  return request<EvidenceRecord | Metric>(`/runs/${runId}/evidence/${kind}/${id}`);
}

export async function listQuality(datasetId: string): Promise<QualityIssue[]> {
  if (isDemo) return demo.listQuality(datasetId);
  const page = await request<Page<QualityIssue>>(
    `/datasets/${datasetId}/quality?page=1&page_size=100`,
  );
  return page.items;
}

export async function getExportPreview(runId: string) {
  if (isDemo) return demo.getExportPreview(runId);
  throw new Error("成果导出接口待联调（Q16）。");
}

export async function simulateRun(datasetId: string): Promise<Job<void>> {
  if (isDemo) return demo.simulateRun(datasetId);
  return request<Job<void>>("/runs", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId }),
  });
}

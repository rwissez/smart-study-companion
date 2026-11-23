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
import { request, requestFile, setAuthToken } from "./client";
import * as demo from "./demo/demoService";
import { demoMaterials } from "./demo/fixtures/materials";

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

let selectedDatasetId: string | undefined;

interface SessionResponse {
  session_token: string;
  scope: SessionInfo["scope"] & { session_id: string; role: "teacher" | "student" };
}

async function openSession(role: "teacher" | "student", datasetId?: string, studentId?: string): Promise<SessionInfo> {
  const response = await request<SessionResponse>("/demo-sessions", {
    method: "POST",
    body: JSON.stringify({ role, dataset_id: datasetId ?? null, student_id: studentId ?? null }),
  });
  return { session_id: response.scope.session_id, session_token: response.session_token, role: response.scope.role, scope: response.scope };
}

export async function listIdentities(): Promise<IdentityOptions> {
  if (isDemo) return demo.listIdentities();
  const bootstrap = await openSession("teacher");
  setAuthToken(bootstrap.session_token);
  const datasets = await listDatasets();
  const first = datasets[0];
  if (!first) throw new Error("服务里还没有可用的数据集。");
  selectedDatasetId = first.dataset_id;
  const classes = await listClasses(first.dataset_id);
  const students: RosterStudent[] = [];
  let page = 1;
  let total: number;
  do {
    const roster = await request<Page<RosterStudent>>(
      `/datasets/${first.dataset_id}/students?page=${page}&page_size=200`,
    );
    students.push(...roster.items);
    total = roster.total;
    if (roster.items.length === 0) break;
    page += 1;
  } while (students.length < total);
  return { dataset: first, classes, students };
}

export async function createSession(
  role: "teacher" | "student",
  studentId?: string,
  datasetId?: string,
): Promise<SessionInfo> {
  if (isDemo) return demo.createSession(role, studentId);
  return openSession(role, datasetId ?? selectedDatasetId, role === "student" ? studentId : undefined);
}

export async function listDatasets(): Promise<DatasetSummary[]> {
  if (isDemo) return demo.listDatasets();
  const result = await request<{ items: DatasetSummary[] }>("/datasets");
  return result.items;
}

export async function listRuns(datasetId: string): Promise<RunSummary[]> {
  if (isDemo) return demo.listRuns(datasetId);
  const result = await request<{ items: RunSummary[] }>(`/datasets/${datasetId}/runs`);
  return result.items;
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
  const result = await request<{ items: ClassSummary[] }>(`/datasets/${datasetId}/classes`);
  return result.items;
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
  if (isDemo) return { ...await demo.getCatalog(), materials: Object.values(demoMaterials) };
  const data = await request<{
    catalog_version: string;
    materials?: Catalog["materials"];
    topics: Array<{ topic_id: string; title: string; goal?: string }>;
    indicators: Array<Pick<Catalog["indicators"][number], "indicator_id" | "topic_id" | "description"> & Partial<Catalog["indicators"][number]>>;
    links: Array<{ source_id: string; target_id: string; relation: "supports" | "prerequisite"; basis: string; description?: string }>;
  }>(
    catalogVersion ? `/catalog?catalog_version=${catalogVersion}` : "/catalog",
  );
  return {
    catalog_version: data.catalog_version,
    materials: data.materials,
    topics: data.topics.map((topic) => ({ topic_id: topic.topic_id, name: topic.title, goal: topic.goal ?? "" })),
    indicators: data.indicators.map((indicator) => ({ curriculum_ref: "", curriculum_page: "", textbook_pages: "", ...indicator })),
    links: data.links.map((link) => ({ from_id: link.source_id, to_id: link.target_id, relation: link.relation, basis: link.basis, description: link.description })),
  };
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
  return request<RecommendationBundle>(`/runs/${runId}/students/${studentId}/recommendations?node_id=${encodeURIComponent(nodeId)}`);
}

export async function uploadDataset(files: Record<string, File>, requestId: string): Promise<{ dataset_id: string; catalog_version: string; node_ids: string[] }> {
  const body = new FormData();
  Object.entries(files).forEach(([name, file]) => body.append(name, file));
  return request("/datasets", { method: "POST", body, headers: { "Idempotency-Key": requestId } });
}

export async function runAnalysis(datasetId: string, catalogVersion: string, nodeIds: string[], requestId: string): Promise<{ run_id: string; status: string; error?: { message: string } | null }> {
  return request("/runs", { method: "POST", headers: { "Idempotency-Key": requestId }, body: JSON.stringify({ dataset_id: datasetId, catalog_version: catalogVersion, node_ids: nodeIds }) });
}

export async function generateRecommendations(runId: string, studentId: string, nodeId: string, requestId: string): Promise<void> {
  await request(`/runs/${runId}/students/${studentId}/recommendations`, { method: "POST", headers: { "Idempotency-Key": requestId }, body: JSON.stringify({ node_id: nodeId, mode: "rule" }) });
}

export async function reviseRecommendation(
  bundle: RecommendationBundle, value: Recommendation, reason: string, baseRevision: number, publish: boolean,
): Promise<void> {
  await request(`/runs/${bundle.run_id}/students/${encodeURIComponent(bundle.student_id)}/recommendation-revisions`, {
    method: "POST",
    body: JSON.stringify({
      node_id: bundle.node_id, recommendation_id: value.recommendation_id, base_revision: baseRevision, reason, publish,
      changes: { goal: value.goal, reason: value.reason, activities: value.activities, adjustment_conditions: value.adjustment_conditions },
    }),
  });
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

export async function getExportPreview(runId: string, publishedOnly = false) {
  if (isDemo) return demo.getExportPreview(runId);
  const result = await request<{
    catalog_version: string; created_at: string; rule_version: string;
    limitations: string[]; files: Array<{ path: string }>;
  }>(`/runs/${runId}/exports?published_only=${publishedOnly}`);
  return { ...result, files: ["manifest.json", ...result.files.map((file) => file.path)] };
}

export function downloadExport(runId: string, publishedOnly = false) {
  return requestFile(`/runs/${runId}/exports/download?published_only=${publishedOnly}`);
}

export interface ModelSettings {
  configured: boolean;
  analysis_context_available?: boolean;
  url?: string;
  model_id?: string;
  has_api_key?: boolean;
}

export function getModelSettings(): Promise<ModelSettings> {
  return isDemo ? Promise.resolve({ configured: false }) : request("/model-settings");
}

export function saveModelSettings(settings: { url: string; model_id: string; api_key: string }) {
  return request<ModelSettings>("/model-settings", { method: "PUT", body: JSON.stringify(settings) });
}

export function testModelSettings() {
  return request<{ connected: boolean }>("/model-settings/test", { method: "POST" });
}

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type ChatContext = { run_id: string; node_id: string; subject_type: "student" | "class"; subject_id: string };

export function sendChat(messages: ChatMessage[], analysisContext?: ChatContext) {
  return request<{ text: string }>("/chat", {
    method: "POST",
    body: JSON.stringify({ messages, analysis_context: analysisContext }),
  });
}

export async function simulateRun(datasetId: string): Promise<Job<void>> {
  if (isDemo) return demo.simulateRun(datasetId);
  return request<Job<void>>("/runs", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId }),
  });
}

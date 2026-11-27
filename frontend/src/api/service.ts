import { request, requestBlob, requestEnvelope, type ApiResult } from "./client";
import * as demo from "./demo/demoService";
import type {
  AlertItem,
  AnswerRecordListItem,
  AttemptResponse,
  AttemptView,
  Catalog,
  ChatMessageRecord,
  ChatReply,
  ClassComparisonView,
  ClassResultView,
  ClassSummary,
  CompleteActivityResponse,
  ConversationSummary,
  DatasetSummary,
  DemoRoster,
  EvidenceDetail,
  EvidenceRef,
  ExportCreateResponse,
  ExportPreview,
  HintResponse,
  ImportReceipt,
  JobDetail,
  JobEventPage,
  KnowledgeQualityReport,
  KnowledgeReviewItem,
  KnowledgeSnapshotView,
  LearningEventView,
  MaterialView,
  MicroLessonCompleteResponse,
  MicroLessonView,
  ModelSettings,
  Page,
  PlanView,
  ProgressComparison,
  QualityIssue,
  RosterStudent,
  RunSummary,
  SessionInfo,
  StartActivityResponse,
  StudentResultView,
  SubmitResponse,
} from "./types";

/** VITE_DATA_SOURCE 默认 api；只有显式写 demo 才进入演示数据。 */
export type DataSource = "api" | "demo" | "invalid";

export const dataSource: DataSource = (() => {
  const raw = ((import.meta.env.VITE_DATA_SOURCE as string | undefined) ?? "api").trim().toLowerCase();
  if (raw === "api") return "api";
  if (raw === "demo") return "demo";
  return "invalid";
})();

export const isDemo = dataSource === "demo";

const query = (params: Record<string, string | number | null | undefined>): string => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
};

const segment = (value: string) => encodeURIComponent(value);

// ------------------------------------------------------------------ 身份与范围

export async function getHealth(): Promise<{ status: string; schema_version: string }> {
  if (isDemo) return { status: "demo", schema_version: "1.1" };
  return request("/health");
}

export async function listDatasets(): Promise<DatasetSummary[]> {
  if (isDemo) return demo.listDatasets();
  const data = await request<{ items: DatasetSummary[] }>("/datasets");
  return data.items;
}

export async function getDemoRoster(datasetId: string): Promise<DemoRoster> {
  if (isDemo) return demo.getDemoRoster(datasetId);
  return request(`/demo-sessions/roster${query({ dataset_id: datasetId })}`);
}

export async function createSession(
  role: "teacher" | "student",
  options: { datasetId?: string | null; studentId?: string | null } = {},
): Promise<SessionInfo> {
  if (isDemo) return demo.createSession(role, options);
  return request<SessionInfo>("/demo-sessions", {
    method: "POST",
    body: JSON.stringify({
      role,
      dataset_id: options.datasetId ?? null,
      student_id: role === "student" ? options.studentId ?? null : null,
    }),
  });
}

// --------------------------------------------------------------- 教师班级总览

export async function listClasses(datasetId: string): Promise<ClassSummary[]> {
  if (isDemo) return demo.listClasses(datasetId);
  const data = await request<{ items: ClassSummary[] }>(`/datasets/${segment(datasetId)}/classes`);
  return data.items;
}

export async function listRuns(datasetId: string): Promise<RunSummary[]> {
  if (isDemo) return demo.listRuns(datasetId);
  const data = await request<{ items: RunSummary[] }>(`/datasets/${segment(datasetId)}/runs`);
  return data.items;
}

export async function getClassProfile(
  datasetId: string,
  classId: string,
  nodeId: string | null,
  options: { signal?: AbortSignal; analysisVersion?: string | null } = {},
): Promise<{ result: ClassResultView; freshness: string; limitations: string[] }> {
  if (isDemo) return demo.getClassProfile(datasetId, classId, nodeId);
  const data = await request<{ result: ClassResultView; freshness: string; limitations: string[] }>(
    `/datasets/${segment(datasetId)}/classes/${segment(classId)}/profile${query({
      node_id: nodeId,
      analysis_version: options.analysisVersion,
    })}`,
    { signal: options.signal },
  );
  return data;
}

export async function listStudents(
  datasetId: string,
  options: { classId?: string | null; page?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<Page<RosterStudent>> {
  if (isDemo) return demo.listStudents(datasetId, options);
  return request<Page<RosterStudent>>(
    `/datasets/${segment(datasetId)}/students${query({
      class_id: options.classId,
      page: options.page ?? 1,
      page_size: options.pageSize ?? 50,
    })}`,
    { signal: options.signal },
  );
}

export async function compareClasses(
  runId: string,
  options: { nodeId: string; leftClassId: string; rightClassId: string; indicatorId?: string | null },
): Promise<ClassComparisonView> {
  if (isDemo) return demo.compareClasses(runId, options);
  return request<ClassComparisonView>(
    `/runs/${segment(runId)}/classes/compare${query({
      node_id: options.nodeId,
      left_class_id: options.leftClassId,
      right_class_id: options.rightClassId,
      indicator_id: options.indicatorId,
    })}`,
  );
}

export async function listAlerts(datasetId: string): Promise<AlertItem[]> {
  if (isDemo) return demo.listAlerts(datasetId);
  const data = await request<Page<AlertItem>>(`/datasets/${segment(datasetId)}/alerts?page=1&page_size=50`);
  return data.items;
}

// ------------------------------------------------------------- 学生画像与方案

export async function getStudentProfile(
  datasetId: string,
  studentId: string,
  options: { nodeId?: string | null; signal?: AbortSignal; analysisVersion?: string | null } = {},
): Promise<{ result: StudentResultView; freshness: string; limitations: string[] }> {
  if (isDemo) return demo.getStudentProfile(datasetId, studentId, options.nodeId ?? null);
  return request(
    `/datasets/${segment(datasetId)}/students/${segment(studentId)}/profile${query({
      node_id: options.nodeId,
      analysis_version: options.analysisVersion,
    })}`,
    { signal: options.signal },
  );
}

export async function getPlan(
  datasetId: string,
  studentId: string,
  options: { nodeId?: string | null; planVersion?: string | null; signal?: AbortSignal } = {},
): Promise<PlanView> {
  if (isDemo) return demo.getPlan(datasetId, studentId, options);
  return request<PlanView>(
    `/datasets/${segment(datasetId)}/students/${segment(studentId)}/plan${query({
      node_id: options.nodeId,
      plan_version: options.planVersion,
    })}`,
    { signal: options.signal },
  );
}

export async function getProgress(
  datasetId: string,
  studentId: string,
  options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<Page<ProgressComparison>> {
  if (isDemo) return demo.getProgress(datasetId, studentId, options);
  return request<Page<ProgressComparison>>(
    `/datasets/${segment(datasetId)}/students/${segment(studentId)}/progress${query({
      page: options.page ?? 1,
      page_size: options.pageSize ?? 20,
    })}`,
    { signal: options.signal },
  );
}

export async function getAnswerRecords(
  runId: string,
  studentId: string,
  options: { nodeId: string; page?: number; pageSize?: number; analysisVersion?: string | null },
): Promise<Page<AnswerRecordListItem>> {
  if (isDemo) return demo.getAnswerRecords(runId, studentId, options);
  return request<Page<AnswerRecordListItem>>(
    `/runs/${segment(runId)}/students/${segment(studentId)}/answer-records${query({
      node_id: options.nodeId,
      page: options.page ?? 1,
      page_size: options.pageSize ?? 20,
      analysis_version: options.analysisVersion,
    })}`,
  );
}

export async function listLearningEvents(
  datasetId: string,
  studentId: string,
  options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<Page<LearningEventView>> {
  if (isDemo) return demo.listLearningEvents(datasetId, studentId, options);
  return request<Page<LearningEventView>>(
    `/datasets/${segment(datasetId)}/students/${segment(studentId)}/learning-events${query({
      page: options.page ?? 1,
      page_size: options.pageSize ?? 200,
    })}`,
    { signal: options.signal },
  );
}

// --------------------------------------------------------------- 课程体系与知识

export async function getCatalog(catalogVersion?: string | null): Promise<Catalog> {
  if (isDemo) return demo.getCatalog();
  return request<Catalog>(`/catalog${query({ catalog_version: catalogVersion })}`);
}

export async function getKnowledge(
  datasetId: string,
  options: { knowledgeVersion?: string | null; signal?: AbortSignal } = {},
): Promise<KnowledgeSnapshotView> {
  if (isDemo) return demo.getKnowledge();
  return request<KnowledgeSnapshotView>(
    `/datasets/${segment(datasetId)}/knowledge${query({ knowledge_version: options.knowledgeVersion })}`,
    { signal: options.signal },
  );
}

export async function listKnowledgeReviews(datasetId: string): Promise<Page<KnowledgeReviewItem>> {
  if (isDemo) return demo.listKnowledgeReviews();
  return request<Page<KnowledgeReviewItem>>(
    `/datasets/${segment(datasetId)}/knowledge/reviews?page=1&page_size=50`,
  );
}

export async function getKnowledgeQuality(datasetId: string): Promise<KnowledgeQualityReport> {
  if (isDemo) return demo.getKnowledgeQuality();
  return request<KnowledgeQualityReport>(`/datasets/${segment(datasetId)}/knowledge/quality`);
}

// ------------------------------------------------------------------- 数据管理

export async function listQuality(datasetId: string): Promise<Page<QualityIssue>> {
  if (isDemo) return demo.listQuality(datasetId);
  return request<Page<QualityIssue>>(`/datasets/${segment(datasetId)}/quality?page=1&page_size=100`);
}

export async function getAnalysisQuality(
  runId: string,
  options: { nodeId: string; classId?: string | null },
): Promise<Record<string, unknown>> {
  if (isDemo) return demo.getAnalysisQuality();
  return request<Record<string, unknown>>(
    `/runs/${segment(runId)}/quality/analysis${query({ node_id: options.nodeId, class_id: options.classId })}`,
  );
}

export async function listJobs(
  datasetId: string,
  options: { page?: number; pageSize?: number } = {},
): Promise<Page<JobDetail>> {
  if (isDemo) return demo.listJobs(datasetId);
  return request<Page<JobDetail>>(
    `/jobs${query({ dataset_id: datasetId, page: options.page ?? 1, page_size: options.pageSize ?? 20 })}`,
  );
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobDetail> {
  if (isDemo) return demo.getJob(jobId);
  return request<JobDetail>(`/jobs/${segment(jobId)}`, { signal });
}

export async function getJobEvents(jobId: string, afterSequence = 0): Promise<JobEventPage> {
  if (isDemo) return demo.getJobEvents(jobId, afterSequence);
  return request<JobEventPage>(
    `/jobs/${segment(jobId)}/events${query({ after_sequence: afterSequence })}`,
  );
}

export async function cancelJob(jobId: string): Promise<JobDetail> {
  if (isDemo) return demo.cancelJob(jobId);
  return request<JobDetail>(`/jobs/${segment(jobId)}/cancel`, { method: "POST" });
}

export async function retryJob(jobId: string, idempotencyKey: string): Promise<JobDetail> {
  if (isDemo) return demo.retryJob(jobId);
  return request<JobDetail>(`/jobs/${segment(jobId)}/retry`, {
    method: "POST",
    body: JSON.stringify({}),
    idempotencyKey,
  });
}

export interface ImportMetadata {
  intent: "new_family" | "append_to_family" | "auto_detect";
  dataset_family_id?: string | null;
  expected_parent_dataset_id?: string | null;
}

export async function createImport(
  files: File[],
  metadata: ImportMetadata,
  idempotencyKey: string,
): Promise<ImportReceipt> {
  if (isDemo) throw new Error("演示模式不会执行真实上传。");
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("metadata", JSON.stringify(metadata));
  return request<ImportReceipt>("/imports", { method: "POST", body, idempotencyKey });
}

export async function getImport(importId: string): Promise<ImportReceipt> {
  if (isDemo) throw new Error("演示模式没有导入记录。");
  return request<ImportReceipt>(`/imports/${segment(importId)}`);
}

export async function confirmImport(
  importId: string,
  payload: {
    file_mappings: Array<{ file_id: string; sheet_name: string | null; role: string }>;
    intent: ImportMetadata["intent"];
    dataset_family_id?: string | null;
    expected_parent_dataset_id?: string | null;
  },
): Promise<ImportReceipt> {
  if (isDemo) throw new Error("演示模式不能确认导入。");
  return request<ImportReceipt>(`/imports/${segment(importId)}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// --------------------------------------------------------------------- 证据

export async function resolveEvidence(
  ref: EvidenceRef,
  context?: { student_id?: string | null; class_id?: string | null; activity_id?: string | null; plan_version?: string | null },
  signal?: AbortSignal,
): Promise<EvidenceDetail> {
  if (isDemo) return demo.resolveEvidence(ref);
  return request<EvidenceDetail>("/evidence/resolve", {
    method: "POST",
    body: JSON.stringify({ ref, context: context ?? null }),
    signal,
  });
}

// ----------------------------------------------------------------- 学习闭环

export async function getMaterial(
  materialId: string,
  options: { catalogVersion?: string | null; activityId?: string | null; planVersion?: string | null } = {},
): Promise<MaterialView> {
  if (isDemo) return demo.getMaterial(materialId);
  return request<MaterialView>(
    `/materials/${segment(materialId)}${query({
      catalog_version: options.catalogVersion,
      activity_id: options.activityId,
      plan_version: options.planVersion,
    })}`,
  );
}

export async function startActivity(
  planVersion: string,
  activityId: string,
): Promise<StartActivityResponse> {
  if (isDemo) return demo.startActivity(planVersion, activityId);
  return request<StartActivityResponse>(
    `/plans/${segment(planVersion)}/activities/${segment(activityId)}/start`,
    { method: "POST" },
  );
}

export async function createAttempt(planVersion: string, activityId: string): Promise<AttemptView> {
  if (isDemo) return demo.createAttempt(planVersion, activityId);
  return request<AttemptView>(
    `/plans/${segment(planVersion)}/activities/${segment(activityId)}/attempts`,
    { method: "POST" },
  );
}

export async function getAttempt(attemptId: string): Promise<AttemptResponse> {
  if (isDemo) return demo.getAttempt(attemptId);
  return request<AttemptResponse>(`/attempts/${segment(attemptId)}`);
}

export async function submitAttempt(
  attemptId: string,
  payload: { final_answer?: string | null; solution_steps?: string | null; unanswered: boolean },
  idempotencyKey: string,
): Promise<SubmitResponse> {
  if (isDemo) return demo.submitAttempt(attemptId, payload);
  return request<SubmitResponse>(`/attempts/${segment(attemptId)}/submit`, {
    method: "POST",
    body: JSON.stringify(payload),
    idempotencyKey,
  });
}

export async function requestHint(
  attemptId: string,
  level: number,
  allowAssisted: boolean,
  idempotencyKey: string,
): Promise<HintResponse> {
  if (isDemo) return demo.requestHint(attemptId, level, allowAssisted);
  return request<HintResponse>(`/attempts/${segment(attemptId)}/hints`, {
    method: "POST",
    body: JSON.stringify({ level, allow_assisted: allowAssisted }),
    idempotencyKey,
  });
}

export async function completeActivity(
  planVersion: string,
  activityId: string,
): Promise<CompleteActivityResponse> {
  if (isDemo) return demo.completeActivity(planVersion, activityId);
  return request<CompleteActivityResponse>(
    `/plans/${segment(planVersion)}/activities/${segment(activityId)}/complete`,
    { method: "POST" },
  );
}

// --------------------------------------------------------------- 模型与对话

export async function getModelSettings(): Promise<ModelSettings> {
  if (isDemo) return demo.getModelSettings();
  return request<ModelSettings>("/model-settings");
}

export async function saveModelSettings(payload: {
  provider?: string;
  url?: string;
  model_id?: string;
  api_key?: string;
  clear_api_key?: boolean;
}): Promise<ModelSettings> {
  if (isDemo) return demo.saveModelSettings();
  return request<ModelSettings>("/model-settings", { method: "PUT", body: JSON.stringify(payload) });
}

export async function testModelSettings(): Promise<{ connected: boolean; message?: string }> {
  if (isDemo) return demo.testModelSettings();
  return request<{ connected: boolean; message?: string }>("/model-settings/test", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function sendChat(payload: {
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  conversation_id?: string | null;
  run_id?: string | null;
  node_id?: string | null;
  student_id?: string | null;
}): Promise<ChatReply> {
  if (isDemo) return demo.sendChat();
  return request<ChatReply>("/chat", { method: "POST", body: JSON.stringify(payload) });
}

export async function listConversations(): Promise<Page<ConversationSummary>> {
  if (isDemo) return { items: [], total: 0, page: 1, page_size: 20 };
  return request<Page<ConversationSummary>>("/conversations?page=1&page_size=50");
}

export async function getConversationMessages(conversationId: string): Promise<ChatMessageRecord[]> {
  if (isDemo) return [];
  const data = await request<{ items: ChatMessageRecord[] }>(
    `/conversations/${segment(conversationId)}/messages`,
  );
  return data.items;
}

// --------------------------------------------------------------------- 微课堂

export async function getMicroLesson(
  runId: string,
  studentId: string,
  activityId: string,
  planVersion: string,
): Promise<MicroLessonView> {
  if (isDemo) throw new Error("演示模式没有微课堂内容。");
  return request<MicroLessonView>(
    `/runs/${segment(runId)}/students/${segment(studentId)}/activities/${segment(activityId)}/micro-lesson${query({ plan_version: planVersion })}`,
  );
}

export async function completeMicroLesson(
  runId: string,
  studentId: string,
  activityId: string,
  payload: { plan_version: string; content_version: string },
): Promise<MicroLessonCompleteResponse> {
  if (isDemo) throw new Error("演示模式没有微课堂内容。");
  return request<MicroLessonCompleteResponse>(
    `/runs/${segment(runId)}/students/${segment(studentId)}/activities/${segment(activityId)}/micro-lesson/complete`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

// ----------------------------------------------------------------------- 导出

export async function getExportPreview(runId: string): Promise<ExportPreview> {
  if (isDemo) return { items: [], total: 0 };
  return request<ExportPreview>(`/runs/${segment(runId)}/exports`);
}

export async function createExport(runId: string): Promise<ExportCreateResponse> {
  if (isDemo) throw new Error("演示模式不会生成真实导出。");
  return request<ExportCreateResponse>(`/runs/${segment(runId)}/exports`, {
    method: "POST",
    body: JSON.stringify({ format: "zip" }),
  });
}

export async function downloadExport(exportId: string): Promise<{ blob: Blob; filename: string | null }> {
  if (isDemo) throw new Error("演示模式没有可下载的导出。");
  return requestBlob(`/exports/${segment(exportId)}/download`);
}

/** 供任务页带 warnings 展示；当前只有质检查询需要保留包络。 */
export async function qualityWithWarnings(path: string): Promise<ApiResult<unknown>> {
  return requestEnvelope(path);
}

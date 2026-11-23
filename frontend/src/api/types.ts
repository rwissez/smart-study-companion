/**
 * 临时接口类型（占位契约）。
 *
 * 字段逐条取自 docs/team（项目基本材料/团队开发手册）中的 08_接口协议.md、
 * 09_智能体与工具设计.md 与 11_统一实施规范.md，命名与含义保持与文字契约一致。
 *
 * 待 A 交付可机读契约（Pydantic → openapi.json）后，本文件由生成脚本整体替换，
 * 页面与组件不得因此改写；组件只依赖这里导出的类型名。
 *
 * 维护规则：
 * - 不得为了让页面跑通而在本文件里新增业务字段。
 * - 发现缺字段，登记到 docs/decisions/open_questions.md。
 */

export type Role = "teacher" | "student";
export type GradingStatus = "已评分" | "待评分" | "未作答";
export type EvidenceKind = "record" | "metric" | "material" | "curriculum";
export type EvidenceStatus = "sufficient" | "insufficient" | "conflicting";
export type Performance = "observed_strength" | "needs_practice" | "undetermined";
export type ReviewStatus = "draft" | "validated" | "needs_review";
export type GenerationMethod = "rule" | "model";
export type RunStatus = "queued" | "running" | "succeeded" | "failed";
export type IssueSeverity = "info" | "warning" | "error";

/** 08：成功响应统一包络 */
export interface ApiEnvelope<T> {
  schema_version: string;
  request_id: string;
  data: T;
  warnings: ApiWarning[];
}

/** 08：warnings 每项为 code、message 及可选 issue_id */
export interface ApiWarning {
  code: string;
  message: string;
  issue_id?: string | null;
}

/** 08：失败响应包络 */
export interface ApiErrorBody {
  schema_version: string;
  request_id: string;
  error: {
    code: string;
    message: string;
    details?: Array<{ field?: string; reason?: string }>;
  };
}

/** 08：EvidenceRef，record/metric 带 run_id，material/curriculum 带 catalog_version */
export interface EvidenceRef {
  kind: EvidenceKind;
  id: string;
  run_id?: string | null;
  catalog_version?: string | null;
}

/** 08：Metric，每项统计保留分子、分母、范围与限制 */
export interface MetricScope {
  student_id?: string | null;
  class_id?: string | null;
  node_id: string;
  indicator_id?: string | null;
}

export interface Metric {
  metric_id: string;
  name: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  unit: "ratio" | "count" | "score";
  scope: MetricScope;
  evidence_refs: EvidenceRef[];
  excluded_event_count: number;
  limitations: string[];
}

/** 08：Finding，表现与证据是否充分分成两个字段 */
export interface Finding {
  finding_id: string;
  indicator_id: string;
  description: string;
  evidence_status: EvidenceStatus;
  performance: Performance;
  evidence_refs: EvidenceRef[];
  limitations: string[];
}

/** 08：QualityIssue */
export interface QualityIssue {
  issue_id: string;
  code: string;
  severity: IssueSeverity;
  record_ids: string[];
  event_id?: string | null;
  message: string;
  action: "retain" | "exclude_score" | "quarantine_event" | "block_import";
  rule_version: string;
}

/** 08：Material（学生端响应会裁掉参考答案、解析与评分点） */
export interface SourceLocator {
  source_file: string;
  pdf_page?: number | null;
  printed_page?: number | null;
  verification_status: "verified" | "unverified";
}

export interface RubricPoint {
  point_id: string;
  description: string;
  score: number;
}

export interface Material {
  material_id: string;
  material_type: "question" | "card" | "textbook";
  topic_ids: string[];
  indicator_ids: string[];
  content: string;
  options?: string[];
  source_locator: SourceLocator;
  reference_answer?: string | null;
  reference_explanation?: string | null;
  max_score?: number | null;
  rubric_points?: RubricPoint[] | null;
  reviewed_variant_tag?: string | null;
}

/** 08：StudentResult（响应 data 内还带 dataset_id、run_id、catalog_version） */
export interface StudentResult {
  dataset_id: string;
  run_id: string;
  catalog_version: string;
  student_id: string;
  class_id: string;
  node_id: string;
  metrics: Metric[];
  findings: Finding[];
  limitations: string[];
}

/** 08：ClassResult */
export interface ClassResult {
  dataset_id: string;
  run_id: string;
  catalog_version: string;
  class_id: string;
  node_id: string;
  roster_count: number;
  observed_student_count: number;
  metrics: Metric[];
  findings: Finding[];
  limitations: string[];
}

/** 08：课程目录 */
export interface Topic {
  topic_id: string;
  name: string;
  goal: string;
}

export interface Indicator {
  indicator_id: string;
  topic_id: string;
  description: string;
  curriculum_ref: string;
  curriculum_page: string;
  textbook_pages: string;
  textbook_refs?: SourceLocator[];
}

export interface CatalogLink {
  from_id: string;
  to_id: string;
  relation: "supports" | "prerequisite";
  basis: string;
  description?: string;
}

export interface Catalog {
  materials?: Array<Pick<Material, "material_id" | "material_type" | "indicator_ids" | "content" | "options">>;
  catalog_version: string;
  topics: Topic[];
  indicators: Indicator[];
  links: CatalogLink[];
}

/** 08：Recommendation 与活动 */
export interface Activity {
  order: number;
  material_id: string;
  purpose: string;
  hint_levels: string[];
  observation_points: string[];
  previously_attempted: boolean;
}

export interface AdjustmentCondition {
  observable_condition: string;
  next_action: string;
}

export interface Recommendation {
  recommendation_id: string;
  indicator_id: string;
  finding_ids: string[];
  goal: string;
  reason: string;
  activities: Activity[];
  adjustment_conditions: AdjustmentCondition[];
  review_status: ReviewStatus;
  generation_method: GenerationMethod;
}

export interface RecommendationBundle {
  dataset_id: string;
  run_id: string;
  catalog_version: string;
  student_id: string;
  node_id: string;
  recommendations: Recommendation[];
  validation_issues: Array<{ code: string; message: string }>;
  pending_review?: boolean;
  needs_regeneration?: boolean;
  available_material_ids?: string[];
  materials?: Catalog["materials"];
  revisions?: Record<string, RecommendationRevision[]>;
}

export interface RecommendationRevision {
  recommendation: Recommendation;
  reason: string;
  revision?: number;
  published?: boolean;
  created_at?: string;
}

/** 11：数据集、运行与状态 */
export interface DatasetSummary {
  dataset_id: string;
  created_at: string;
  status: "ready" | "ready_with_issues" | "blocked";
  student_count: number;
  record_count: number;
  synthetic?: boolean;
}

export interface RunSummary {
  run_id: string;
  dataset_id: string;
  catalog_version: string;
  status: RunStatus;
  node_ids: string[];
  created_at: string;
  message?: string | null;
}

export interface ClassSummary {
  class_id: string;
  roster_count: number;
}

export interface RosterStudent {
  student_id: string;
  class_id: string;
  grade: number;
  observed_event_count: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 08：会话（本地模拟身份，非生产认证） */
export interface SessionScope {
  dataset_id: string | null;
  student_id: string | null;
  synthetic?: boolean | null;
}

export interface SessionInfo {
  session_id: string;
  session_token: string;
  role: Role;
  scope: SessionScope;
}

/** 08：任务 */
export interface Job<T = unknown> {
  job_id: string;
  status: RunStatus;
  result?: T | null;
  error?: { code: string; message: string } | null;
}

/** 08：证据查询返回的作答记录（教师视角，学生端会被裁剪） */
export interface EvidenceRecord {
  record_id: string;
  student_id: string;
  material_id: string;
  node_id: string;
  indicator_id: string;
  grading_status: GradingStatus;
  score: number | null;
  max_score: number;
  final_answer: string | null;
  solution_steps: string[] | null;
  rubric_scores: Array<{ point_id: string; score: number }> | null;
  submitted_at: string;
  disposition: "selected" | "history" | "quarantined";
  disposition_note?: string | null;
}

/** 09：对话任务结果（示例模式不产生回答，只用于展示未配置状态） */
export interface AgentAnswer {
  agent_request_id: string;
  run_id: string;
  dataset_id: string;
  catalog_version: string;
  answer: string;
  evidence_refs: EvidenceRef[];
  recommendation_ids: string[];
  tool_trace: Array<{
    call_id: string;
    tool_name: string;
    status: "succeeded" | "failed";
    duration_ms: number;
    evidence_refs: EvidenceRef[];
  }>;
  limitations: string[];
  generation_method: GenerationMethod;
}

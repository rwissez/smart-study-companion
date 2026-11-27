/**
 * 前端视图类型。字段以 docs/api-surface.md 与后端实际响应为准。
 * 规则：不为让页面跑通编造字段；接口缺口写进交付报告，不在此处假装存在。
 */

export type Role = "teacher" | "student";

export interface ApiWarning {
  code: string;
  message: string;
  issue_id?: string | null;
}

export interface ApiEnvelope<T> {
  schema_version: string;
  request_id: string;
  data: T;
  warnings: ApiWarning[];
}

export interface ApiErrorBody {
  schema_version: string;
  request_id: string;
  error: {
    code: string;
    message: string;
    details?: Array<{ field?: string; reason?: string }> | null;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface SessionScope {
  session_id: string;
  role: Role;
  dataset_id: string | null;
  dataset_family_id: string | null;
  student_id: string | null;
  allowed_class_ids: string[];
  bootstrap: boolean;
  capabilities: string[];
}

export interface SessionInfo {
  session_token: string;
  expires_at: string;
  scope: SessionScope;
}

export interface DemoRoster {
  dataset_id: string;
  classes: Array<{ class_id: string; student_ids: string[] }>;
}

export interface DatasetSummary {
  dataset_id: string;
  dataset_family_id: string | null;
  status: string;
  state?: string | null;
  created_at: string;
  parent_id?: string | null;
  synthetic: boolean;
  display_name: string | null;
  student_count: number;
  class_count: number;
  catalog_version: string | null;
  table_row_counts: Record<string, number>;
}

export interface ClassSummary {
  class_id: string;
  display_name: string;
  roster_count: number;
}

export interface RosterStudent {
  student_id: string;
  class_id: string;
  grade: string | number;
  observed_event_count: number;
  graded_event_count: number;
}

export interface RunSummary {
  run_id: string;
  dataset_id: string;
  catalog_version: string;
  knowledge_version: string | null;
  rule_version: string;
  status: string;
  created_at: string;
  node_ids: string[];
}

export interface EvidenceRef {
  kind: string;
  id: string;
  run_id?: string | null;
  catalog_version?: string | null;
  dataset_id?: string | null;
  knowledge_version?: string | null;
  analysis_version?: string | null;
  plan_version?: string | null;
  material_id?: string | null;
  attempt_id?: string | null;
  curriculum_type?: string | null;
}

export type Unit = "ratio" | "count" | "score";

export interface MetricScope {
  student_id: string | null;
  class_id: string | null;
  node_id: string;
  indicator_id: string | null;
  topic_id: string | null;
  kp_id: string | null;
}

export interface Metric {
  metric_id: string;
  name: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  unit: Unit | string;
  scope: MetricScope;
  evidence_refs: EvidenceRef[];
  excluded_event_count: number;
  limitations: string[];
  evidence_status?: string | null;
  calculation_version?: string | null;
}

export type EvidenceStatus = "sufficient" | "insufficient" | "conflicting" | string;
export type Performance = "observed_strength" | "needs_practice" | "undetermined" | string;

export interface Finding {
  finding_id: string;
  indicator_id: string;
  description: string;
  evidence_status: EvidenceStatus;
  performance: Performance;
  evidence_refs: EvidenceRef[];
  limitations: string[];
  rubric_focus?: string[] | null;
  error_types?: string[] | null;
  diagnostic_details?: string[] | Record<string, unknown> | null;
}

export interface StrategySignal {
  signal_id: string;
  indicator_id: string;
  kp_id?: string | null;
  finding_ids: string[];
  evidence_refs: EvidenceRef[];
  signal_type: string;
  limitations: string[];
}

export type ProgressClassification =
  | "improved"
  | "partially_improved"
  | "unchanged_or_declined"
  | "insufficient";

export interface ProgressComparison {
  comparison_id: string;
  student_id: string;
  baseline_analysis_version: string;
  current_analysis_version: string;
  scope: MetricScope;
  classification: ProgressClassification;
  comparable: boolean;
  baseline_value: number | null;
  current_value: number | null;
  evidence_refs: EvidenceRef[];
  limitations: string[];
  description: string;
}

export interface StudentResultView {
  student_id: string;
  class_id: string;
  node_id: string;
  analysis_version: string;
  run_id: string;
  dataset_id: string;
  dataset_family_id: string | null;
  catalog_version: string;
  knowledge_version: string | null;
  metrics: Metric[];
  findings: Finding[];
  progress: ProgressComparison[];
  strategy_signals: StrategySignal[];
  summary: string | null;
  limitations: string[];
  freshness: string;
  scope_kind: string;
}

export interface DistributionBucket {
  indicator_id: string;
  performance: Performance;
  evidence_status: EvidenceStatus;
  count: number;
  roster_count: number;
  kp_id?: string | null;
}

export interface AttentionStudent {
  student_id: string;
  finding_ids: string[];
  evidence_refs: EvidenceRef[];
  reason: string;
  limitations: string[];
}

export interface ClassResultView {
  class_id: string;
  node_id: string;
  analysis_version: string;
  run_id: string;
  dataset_id: string;
  dataset_family_id: string | null;
  catalog_version: string;
  roster_count: number;
  observed_student_count: number;
  metrics: Metric[];
  findings: Finding[];
  common_findings: Finding[];
  performance_distribution: DistributionBucket[];
  attention_students: AttentionStudent[];
  member_analysis_versions: Array<Record<string, unknown>>;
  summary: string | null;
  limitations: string[];
  freshness: string;
}

export interface ClassComparisonItem {
  indicator_id: string;
  left_value: number | null;
  right_value: number | null;
  delta: number | null;
  left_numerator: number | null;
  left_denominator: number | null;
  right_numerator: number | null;
  right_denominator: number | null;
  limitations: string[];
}

export interface ClassComparisonView {
  class_ids: string[];
  node_id: string;
  member_analysis_versions: Array<{ class_id: string; analysis_version: string }>;
  comparable: boolean;
  items: ClassComparisonItem[];
  limitations: string[];
}

export type ActivityKind = "question" | "study_material" | "micro_lesson";
export type ActivityState = "not_started" | "in_progress" | "completed" | "cancelled";

export interface PlanActivity {
  order: number;
  material_id: string | null;
  purpose: string;
  hint_levels: string[];
  observation_points: string[];
  previously_attempted: boolean;
  activity_id: string;
  group_id: string;
  knowledge_ids: string[];
  activity_kind: ActivityKind;
  state: ActivityState;
  title: string;
  expected_outcome: string;
  estimate_minutes: number;
  dependencies: string[];
  evidence_refs: EvidenceRef[];
  exposure_mode: "practice" | "independent" | null;
  repeat_reason: string | null;
  content_ref: { content_id?: string; content_version?: string } | null;
  content_version: string | null;
  source_label: string | null;
}

export interface AdjustmentCondition {
  observable_condition: string;
  next_action: string;
  group?: string;
}

export interface Recommendation {
  recommendation_id: string;
  indicator_id: string;
  finding_ids: string[];
  goal: string;
  reason: string;
  student_intro: string;
  activities: PlanActivity[];
  adjustment_conditions: AdjustmentCondition[];
  review_status: string;
  generation_method: string;
  strategy_types: string[];
  kp_ids: string[];
  evidence_refs: EvidenceRef[];
  review_info: Record<string, unknown> | null;
  group_id: string;
}

export interface TaskGroup {
  group_id: string;
  goal: string;
  strategy_types: string[];
  activity_ids: string[];
  order: number;
  primary_strategy: string | null;
  auxiliary_strategies: string[];
  goal_target_key: string | null;
  expected_outcome: string;
  estimated_minutes: number;
  required_activity_ids: string[];
  evidence_refs: EvidenceRef[];
  dependencies: string[];
  student_intro: string;
  teacher_reason: string;
}

export interface RecommendationBundle {
  run_id: string;
  dataset_id: string;
  catalog_version: string;
  student_id: string;
  node_id: string;
  recommendations: Recommendation[];
  validation_issues: Array<{ code: string; message: string }>;
  plan_version: string;
  analysis_version: string;
  knowledge_version: string | null;
  parent_plan_version: string | null;
  revision: number;
  publication_status: string;
  created_at: string;
  task_groups: TaskGroup[];
  dataset_family_id: string | null;
  limitations: string[];
}

export interface PlanApplicability {
  applicability_id: string;
  plan_version: string;
  origin_analysis_version: string;
  current_analysis_version: string;
  checked_at: string;
  event_cutoff: number;
  result: string;
  activity_ids: string[];
  evidence_refs: EvidenceRef[];
  limitations: string[];
}

export interface PlanView {
  plan: RecommendationBundle;
  plan_version: string;
  freshness: string;
  limitations: string[];
  analysis_version: string;
  applicability: PlanApplicability | null;
}

export interface MaterialSourceLocator {
  source_file: string;
  pdf_page?: number | null;
  printed_page?: number | null;
  verification_status: string;
  file_id?: string | null;
  source_kind?: string | null;
  chapter?: string | null;
  section?: string | null;
  page_end?: number | null;
}

/** 学生视图永不含 reference_answer / reference_explanation / rubric_points。 */
export interface MaterialView {
  material_id: string;
  material_type: string;
  catalog_version: string;
  topic_ids: string[];
  indicator_ids: string[];
  content: string;
  options: string[];
  question_type: string | null;
  source_label: string | null;
  source_locator: MaterialSourceLocator | null;
  max_score: number;
  difficulty_level: string | null;
  difficulty_status: string | null;
  display_order: number;
}

export interface CatalogTopic {
  topic_id: string;
  title: string;
  goal: string;
}

export interface CatalogIndicator {
  indicator_id: string;
  topic_id: string;
  description: string;
  curriculum_ref: string;
  curriculum_page: string;
  textbook_pages: string;
}

export interface CatalogLink {
  source_id: string;
  target_id: string;
  relation: string;
  basis: string;
  description?: string;
}

export interface Catalog {
  catalog_version: string;
  knowledge_version: string | null;
  topics: CatalogTopic[];
  indicators: CatalogIndicator[];
  materials: MaterialView[];
  links: CatalogLink[];
}

export interface KnowledgePoint {
  kp_id: string;
  name: string;
  aliases: string[];
  kind: string;
  definition: string;
  scope_role: string;
  topic_ids: string[];
  indicator_ids: string[];
  snippet: string;
  source_locators: MaterialSourceLocator[];
  review_info: Record<string, unknown> | null;
  content_hash: string;
}

export interface KnowledgeRelation {
  relation_id?: string;
  source_ids: string[];
  target_id: string;
  relation_type?: string;
  type?: string;
  basis?: string;
  evidence?: string;
  review_info?: Record<string, unknown> | null;
  source_locators?: MaterialSourceLocator[];
}

export interface KnowledgeAnnotation {
  annotation_id: string;
  source_type: string;
  source_id: string;
  target_type: string;
  target_ids: string[];
  knowledge_role: string;
  basis: string;
  evidence: string;
  source_locators: MaterialSourceLocator[];
  review_info: Record<string, unknown> | null;
}

export interface KnowledgeSnapshotView {
  dataset_id: string;
  catalog_version: string;
  knowledge_version: string;
  parent_version: string | null;
  status: string;
  points: KnowledgePoint[];
  relations: KnowledgeRelation[];
  annotations: KnowledgeAnnotation[];
  created_at: string;
  review_summary: Record<string, unknown>;
  limitations: string[];
}

export interface KnowledgeReviewItem {
  review_id: string;
  object_type: string;
  object_id: string;
  candidate_version: string;
  review_info: Record<string, unknown>;
  summary: string;
  source_locators: MaterialSourceLocator[];
}

export interface KnowledgeQualityReport {
  dataset_id: string;
  catalog_version: string;
  knowledge_version: string | null;
  candidate_version: string;
  job_id?: string | null;
  generated_at: string;
  counts: Record<string, number>;
  coverage: Array<Record<string, unknown>>;
  audit?: Record<string, number>;
  issues: Array<Record<string, unknown>>;
  limitations: string[];
}

export interface QualityIssue {
  issue_id: string;
  code: string;
  severity: string;
  record_ids: string[];
  event_id: string | null;
  message: string;
  action: string;
  rule_version: string;
  field?: string | null;
}

export interface JobStep {
  step_key: string;
  label: string;
  status: string;
  message: string | null;
  evidence_refs?: EvidenceRef[];
  started_at: string | null;
  ended_at: string | null;
}

export interface JobRetry {
  root_job_id: string | null;
  retry_of_job_id: string | null;
  retry_job_id: string | null;
  manual_retry_count: number;
  can_retry: boolean;
  retry_block_reason: string | null;
}

export interface JobDetail {
  job_id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  scope: Record<string, string | null>;
  created_at: string;
  updated_at: string;
  result_ref: string | null;
  error: { code?: string; message?: string } | null;
  warnings: ApiWarning[];
  steps: JobStep[];
  review_status: string | null;
  publication_status: string | null;
  plan_version: string | null;
  retry: JobRetry;
  result: Record<string, unknown> | null;
  cancel_requested: boolean;
}

export interface JobEvent {
  sequence: number;
  stage: string;
  status: string;
  message: string;
  occurred_at: string;
}

export interface JobEventPage {
  items: JobEvent[];
  next_sequence: number;
  has_more: boolean;
}

export interface ImportFileReceipt {
  file_id: string;
  original_name: string;
  sha256: string;
  byte_size: number;
  format: string;
  role: string | null;
  sheet_name: string | null;
  status: string;
  reason: string | null;
}

export interface ImportReceipt {
  import_id: string;
  dataset_family_id: string | null;
  dataset_id: string | null;
  classification_status: string;
  input_status: string;
  files: ImportFileReceipt[];
  missing_inputs: string[];
  quality_summary: QualityIssue[];
  job_id: string | null;
  warnings: ApiWarning[];
}

export interface EvidenceDetail {
  ref: EvidenceRef;
  title: string;
  detail_type: "record" | "metric" | "curriculum" | "derived" | "learning";
  detail: Record<string, unknown>;
  summary: string | null;
  related_refs: EvidenceRef[];
  limitations: string[];
  visibility: "full" | "redacted";
}

export interface AttemptView {
  attempt_id: string;
  student_id: string;
  plan_version: string;
  activity_id: string;
  material_id: string;
  status: string;
  submitted_at: string | null;
  final_answer: string | null;
  solution_steps: string | null;
  assistance_status: string;
  hint_max_level: number | null;
  agent_help_count: number | null;
  submission_no: number;
  latest_grade_id: string | null;
}

export interface RubricScore {
  point_id: string;
  description?: string | null;
  score: number | null;
  max_score?: number | null;
}

export interface GradingResultView {
  grade_id: string;
  attempt_id: string;
  revision: number;
  status: string;
  validation_status: string;
  score: number | null;
  max_score: number | null;
  rubric_scores: RubricScore[];
  method: string;
  limitations: string[];
  created_at: string;
}

export interface GroupStatus {
  group_completed?: boolean;
  state?: string;
}

export interface AttemptResponse {
  attempt: AttemptView;
  grade: GradingResultView | null;
  feedback: unknown;
  group_status: GroupStatus | null;
  update_job_id: string | null;
}

export interface SubmitResponse {
  attempt: AttemptView;
  grade: GradingResultView | null;
  job_id: string | null;
  feedback_status: string;
  group_status: GroupStatus | null;
}

export interface HintResponse {
  event_id: string;
  level: number;
  hint: string;
}

export interface StartActivityResponse {
  activity_id: string;
  state: ActivityState;
  event_id: string | null;
  attempt?: AttemptView;
}

export interface CompleteActivityResponse {
  activity_id: string;
  state: ActivityState;
  event_id: string | null;
  group_completed: boolean;
}

export interface LearningEventView {
  event_id: string;
  event_type: string;
  source: string;
  occurred_at: string;
  sequence: number | null;
  student_id: string;
  node_id: string | null;
  plan_version: string | null;
  group_id: string | null;
  activity_id: string | null;
  material_id: string | null;
  attempt_id: string | null;
  summary: string;
  payload: Record<string, unknown>;
}

export interface AnswerRecordListItem {
  record_key: string;
  origin: string;
  node_id: string;
  material_id: string | null;
  record_id: string | null;
  attempt_id: string | null;
  grade_id: string | null;
  grading_status: string;
  score: number | null;
  max_score: number | null;
  occurred_at: string | null;
  evidence_ref: EvidenceRef;
}

export interface AlertItem {
  alert_id: string;
  kind: string;
  status: string;
  summary: string;
  target_ref: Record<string, string>;
  student_id: string | null;
  evidence_refs: EvidenceRef[];
  created_at: string;
}

export interface ModelSettings {
  configured: boolean;
  provider: string | null;
  url: string | null;
  model_id: string | null;
  has_api_key: boolean;
}

// ------------------------------------------------------------------- 对话历史

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  role: string;
  dataset_id: string;
  created_at: string;
  updated_at: string;
  last_message: Record<string, unknown> | null;
}

export interface ChatSegment {
  kind: string;
  text: string;
  evidence_refs: EvidenceRef[];
}

export interface ChatMessageRecord {
  message_id: string;
  conversation_id: string;
  sequence: number;
  role: string;
  content: string;
  status: string;
  created_at: string;
  segments: ChatSegment[];
  job_id: string | null;
  tool_trace: Array<Record<string, unknown>>;
  evidence_refs: EvidenceRef[];
  operation_results: Array<Record<string, unknown>>;
}

export interface ChatReply {
  text: string;
  tool_trace?: Array<Record<string, unknown>>;
  evidence_refs?: EvidenceRef[];
  segments?: ChatSegment[];
  stopped_reason?: string | null;
  task_type?: string;
  route_source?: string;
  conversation_id?: string;
  message_id?: string;
  status?: string;
  operation_results?: Array<Record<string, unknown>>;
}

// --------------------------------------------------------------------- 微课堂

export interface LessonTextComponent {
  type: "text";
  id: string;
  heading?: string | null;
  paragraphs: string[];
}

export interface LessonFormulaComponent {
  type: "formula";
  id: string;
  expr: string;
  caption?: string | null;
}

export interface LessonDerivationComponent {
  type: "derivation";
  id: string;
  mode: string;
  steps: Array<{ expr: string; note: string }>;
}

export interface LessonContrastComponent {
  type: "contrast";
  id: string;
  before: string;
  incorrect: string;
  correct: string;
  note?: string | null;
}

export interface LessonInterval {
  lo: string | null;
  hi: string | null;
  lo_closed: boolean;
  hi_closed: boolean;
}

export interface LessonNumberLineComponent {
  type: "number_line";
  id: string;
  min: string;
  max: string;
  tick: string;
  sets: Array<{ label: string; intervals: LessonInterval[] }>;
  operation: string | null;
  result_index?: number | null;
}

export interface LessonCitationComponent {
  type: "citation";
  id: string;
  labels: Array<{ label: string; source_index?: number | null; evidence_index?: number | null }>;
}

export type LessonComponent =
  | LessonTextComponent
  | LessonFormulaComponent
  | LessonDerivationComponent
  | LessonContrastComponent
  | LessonNumberLineComponent
  | LessonCitationComponent;

export interface MicroLessonView {
  content_id: string;
  content_version: string;
  schema_version: string;
  title: string;
  goal: string;
  components: LessonComponent[];
  safe_sources: Array<Record<string, unknown>>;
  fallback_text: string;
  review_status: string;
  activity_id: string;
  plan_version: string;
}

export interface MicroLessonCompleteResponse {
  activity_id: string;
  state: ActivityState;
  event_id: string | null;
  group_completed: boolean;
}

// --------------------------------------------------------------------- 导出

export interface ExportItem {
  export_id: string;
  snapshot_id: string;
  job_id: string | null;
  run_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  bytes: number | null;
  check_report: Record<string, unknown> | null;
  manifest: Record<string, unknown> | null;
}

export interface ExportPreview {
  items: ExportItem[];
  total: number;
}

export interface ExportCreateResponse {
  export_id: string;
  snapshot_id: string;
  job_id: string;
  status: string;
}


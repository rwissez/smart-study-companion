/**
 * 演示数据源：只在 VITE_DATA_SOURCE=demo 时启用，页面必须同时显示「演示数据」角标。
 * 数据是合成示意，不来自后端，也不用于任何正式验收结论。
 */
import type {
  AlertItem,
  AnswerRecordListItem,
  AttemptResponse,
  AttemptView,
  Catalog,
  ClassComparisonView,
  ClassResultView,
  ClassSummary,
  CompleteActivityResponse,
  DatasetSummary,
  DemoRoster,
  EvidenceDetail,
  EvidenceRef,
  Finding,
  HintResponse,
  JobDetail,
  JobEventPage,
  KnowledgeQualityReport,
  KnowledgeReviewItem,
  KnowledgeSnapshotView,
  LearningEventView,
  MaterialView,
  Metric,
  ModelSettings,
  Page,
  PlanActivity,
  PlanView,
  ProgressComparison,
  QualityIssue,
  RosterStudent,
  RunSummary,
  SessionInfo,
  StartActivityResponse,
  StudentResultView,
  SubmitResponse,
} from "../types";

const DATASET_ID = "demo-dataset";
const RUN_ID = "demo-run-1";
const NODE_ID = "N01";
const STUDENT_ID = "S01";
const CATALOG_VERSION = "demo-catalog-1";
const PLAN_VERSION = "demo-plan-1";
const ANALYSIS_VERSION = "demo-analysis-1";

const classes: ClassSummary[] = [
  { class_id: "DC01", display_name: "七年级 1 班", roster_count: 2 },
  { class_id: "DC02", display_name: "七年级 2 班", roster_count: 1 },
];

const students: RosterStudent[] = [
  { student_id: "S01", class_id: "DC01", grade: "七年级", observed_event_count: 3, graded_event_count: 2 },
  { student_id: "S02", class_id: "DC01", grade: "七年级", observed_event_count: 0, graded_event_count: 0 },
  { student_id: "S03", class_id: "DC02", grade: "七年级", observed_event_count: 2, graded_event_count: 2 },
];

const indicatorNames: Record<string, string> = {
  T01I1: "去括号时的符号处理",
  T01I2: "解方程步骤的完整性",
  T02I1: "解集端点与开闭判断",
};

function metric(name: string, value: number | null, numerator: number | null, denominator: number | null, indicatorId: string, studentId: string | null, classId: string | null): Metric {
  return {
    metric_id: `M-${studentId ?? classId}-${indicatorId}-${name}`,
    name,
    value,
    numerator,
    denominator,
    unit: "ratio",
    scope: { student_id: studentId, class_id: classId, node_id: NODE_ID, indicator_id: indicatorId, topic_id: null, kp_id: null },
    evidence_refs: [],
    excluded_event_count: 0,
    limitations: [],
  };
}

const studentMetrics: Metric[] = [
  metric("valid_score_ratio", 1, 5, 5, "T01I1", STUDENT_ID, null),
  metric("valid_score_ratio", 0.5, 2.5, 5, "T01I2", STUDENT_ID, null),
  metric("valid_score_ratio", null, 0, 0, "T02I1", STUDENT_ID, null),
];

const classMetrics: Metric[] = [
  metric("valid_score_ratio", 0.83, 5, 6, "T01I1", null, "DC01"),
  metric("valid_score_ratio", 0.67, 4, 6, "T01I2", null, "DC01"),
  metric("valid_score_ratio", 0.5, 3, 6, "T02I1", null, "DC01"),
];

const findings: Finding[] = [
  {
    finding_id: "F-STU-S01-N01-T01I2",
    indicator_id: "T01I2",
    description: "解方程时移项后没有写检验步骤，步骤完整性不足。",
    evidence_status: "sufficient",
    performance: "needs_practice",
    evidence_refs: [{ kind: "record", id: "R-DEMO-2", run_id: RUN_ID }],
    limitations: [],
  },
  {
    finding_id: "F-STU-S01-N01-T02I1",
    indicator_id: "T02I1",
    description: "有效不同样题仅 1 道，低于下限 3 道，证据不足不定论。",
    evidence_status: "insufficient",
    performance: "undetermined",
    evidence_refs: [],
    limitations: ["证据不足不等于未掌握"],
  },
];

const classFindings: Finding[] = [
  {
    finding_id: "F-CLS-DC01-N01-T01I2",
    indicator_id: "T01I2",
    description: "多名学生解方程后未写检验，过程分丢失集中在最后一步。",
    evidence_status: "sufficient",
    performance: "needs_practice",
    evidence_refs: [{ kind: "record", id: "R-DEMO-2", run_id: RUN_ID }],
    limitations: ["基于 2 名有记录学生，样本较小"],
  },
];

function activity(
  id: string,
  order: number,
  materialId: string,
  title: string,
  kind: PlanActivity["activity_kind"],
  state: PlanActivity["state"],
  groupId: string,
  hintLevels: string[],
  extra: Partial<PlanActivity> = {},
): PlanActivity {
  return {
    order,
    material_id: materialId,
    purpose: "观察同一指标下的不同问法",
    hint_levels: hintLevels,
    observation_points: ["能否写出完整过程"],
    previously_attempted: false,
    activity_id: id,
    group_id: groupId,
    knowledge_ids: [],
    activity_kind: kind,
    state,
    title,
    expected_outcome: "能独立写出过程",
    estimate_minutes: 4,
    dependencies: [],
    evidence_refs: [],
    exposure_mode: "practice",
    repeat_reason: null,
    content_ref: null,
    content_version: null,
    source_label: "演示题库",
    ...extra,
  };
}

const activities: PlanActivity[] = [
  activity("demo-act-1", 1, "Q1", "化简 -(2x-7)", "question", "not_started", "demo-group-1", [
    "先看括号前的符号，再逐项相乘。",
    "把 -1 分别乘给括号里的两项。",
    "−2x + 7，注意第二项变号。",
  ]),
  activity("demo-act-2", 2, "Q2", "解方程 3x+8=20", "question", "not_started", "demo-group-1", [
    "先把常数项移到右边。",
    "两边同时减 8 得 3x=12。",
    "再同除以 3，x=4。",
  ]),
  activity("demo-act-3", 3, "C1", "去括号的三条规则", "study_material", "not_started", "demo-group-2", []),
];

const materials: Record<string, MaterialView> = {
  Q1: {
    material_id: "Q1",
    material_type: "question",
    catalog_version: CATALOG_VERSION,
    topic_ids: ["T01"],
    indicator_ids: ["T01I1"],
    content: "化简 -(2x-7) 的结果是（）",
    options: ["A. -2x-7", "B. -2x+7", "C. 2x-7", "D. 2x+7"],
    question_type: "choice",
    source_label: "演示题库",
    source_locator: { source_file: "演示题库.csv", verification_status: "unverified" },
    max_score: 5,
    difficulty_level: null,
    difficulty_status: null,
    display_order: 1,
  },
  Q2: {
    material_id: "Q2",
    material_type: "question",
    catalog_version: CATALOG_VERSION,
    topic_ids: ["T01"],
    indicator_ids: ["T01I2"],
    content: "解方程 3x+8=20，x=____",
    options: [],
    question_type: "fill_blank",
    source_label: "演示题库",
    source_locator: { source_file: "演示题库.csv", verification_status: "unverified" },
    max_score: 5,
    difficulty_level: null,
    difficulty_status: null,
    display_order: 2,
  },
  Q3: {
    material_id: "Q3",
    material_type: "question",
    catalog_version: CATALOG_VERSION,
    topic_ids: ["T01"],
    indicator_ids: ["T01I2"],
    content: "解方程 2(x-3)+1=5，并写出检验过程。",
    options: [],
    question_type: "solution",
    source_label: "演示题库",
    source_locator: { source_file: "演示题库.csv", verification_status: "unverified" },
    max_score: 5,
    difficulty_level: null,
    difficulty_status: null,
    display_order: 3,
  },
  C1: {
    material_id: "C1",
    material_type: "card",
    catalog_version: CATALOG_VERSION,
    topic_ids: ["T01"],
    indicator_ids: ["T01I1"],
    content: "去括号的三条规则：括号前是正号，去掉括号各项不变号；括号前是负号，去掉括号各项都变号；括号前有系数，先乘系数再判断符号。",
    options: [],
    question_type: null,
    source_label: "演示材料",
    source_locator: { source_file: "演示材料.csv", verification_status: "unverified" },
    max_score: 0,
    difficulty_level: null,
    difficulty_status: null,
    display_order: 4,
  },
};

const assessment: StudentResultView = {
  student_id: STUDENT_ID,
  class_id: "DC01",
  node_id: NODE_ID,
  analysis_version: ANALYSIS_VERSION,
  run_id: RUN_ID,
  dataset_id: DATASET_ID,
  dataset_family_id: "demo-family",
  catalog_version: CATALOG_VERSION,
  knowledge_version: "demo-knowledge-1",
  metrics: studentMetrics,
  findings,
  progress: [],
  strategy_signals: [],
  summary: null,
  limitations: [],
  freshness: "current",
  scope_kind: "assessment",
};

const classResult: ClassResultView = {
  class_id: "DC01",
  node_id: NODE_ID,
  analysis_version: ANALYSIS_VERSION,
  run_id: RUN_ID,
  dataset_id: DATASET_ID,
  dataset_family_id: "demo-family",
  catalog_version: CATALOG_VERSION,
  roster_count: 2,
  observed_student_count: 1,
  metrics: classMetrics,
  findings: classFindings,
  common_findings: classFindings,
  performance_distribution: [
    { indicator_id: "T01I2", performance: "needs_practice", evidence_status: "sufficient", count: 1, roster_count: 2 },
    { indicator_id: "T02I1", performance: "undetermined", evidence_status: "insufficient", count: 1, roster_count: 2 },
  ],
  attention_students: [
    {
      student_id: "S01",
      finding_ids: ["F-STU-S01-N01-T01I2"],
      evidence_refs: [{ kind: "record", id: "R-DEMO-2", run_id: RUN_ID }],
      reason: "解方程后未写检验，过程分丢失",
      limitations: [],
    },
  ],
  member_analysis_versions: [],
  summary: null,
  limitations: ["名册 2 人中仅 1 人有有效记录，不能推断全班整体表现"],
  freshness: "current",
};

const progressItems: ProgressComparison[] = [
  {
    comparison_id: "demo-progress-1",
    student_id: STUDENT_ID,
    baseline_analysis_version: "demo-analysis-0",
    current_analysis_version: ANALYSIS_VERSION,
    scope: { student_id: STUDENT_ID, class_id: null, node_id: NODE_ID, indicator_id: "T01I2", topic_id: null, kp_id: null },
    classification: "partially_improved",
    comparable: true,
    baseline_value: 0.3,
    current_value: 0.5,
    evidence_refs: [],
    limitations: ["两次测验题目不同，只看同口径指标"],
    description: "解方程步骤完整性有所改善。",
  },
];

const plan: PlanView = {
  plan_version: PLAN_VERSION,
  freshness: "current",
  limitations: ["演示方案基于合成数据"],
  analysis_version: ANALYSIS_VERSION,
  applicability: null,
  plan: {
    run_id: RUN_ID,
    dataset_id: DATASET_ID,
    catalog_version: CATALOG_VERSION,
    student_id: STUDENT_ID,
    node_id: NODE_ID,
    recommendations: [
      {
        recommendation_id: "demo-rec-1",
        indicator_id: "T01I2",
        finding_ids: ["F-STU-S01-N01-T01I2"],
        goal: "用两道不同问法的题，看清过程完整性",
        reason: "解方程后未写检验，需要观察过程",
        student_intro: "今天先来两道小题，不是考试，卡住了也没关系。",
        activities: activities.slice(0, 2),
        adjustment_conditions: [{ observable_condition: "两题过程完整", next_action: "进入下一步", group: "advance" }],
        review_status: "validated",
        generation_method: "rule",
        strategy_types: ["consolidation"],
        kp_ids: [],
        evidence_refs: [],
        review_info: null,
        group_id: "demo-group-1",
      },
    ],
    validation_issues: [],
    plan_version: PLAN_VERSION,
    analysis_version: ANALYSIS_VERSION,
    knowledge_version: "demo-knowledge-1",
    parent_plan_version: null,
    revision: 1,
    publication_status: "published",
    created_at: "2026-11-20T08:00:00+00:00",
    task_groups: [
      {
        group_id: "demo-group-1",
        goal: "用两道不同问法的题，看清过程完整性",
        strategy_types: ["consolidation"],
        activity_ids: ["demo-act-1", "demo-act-2"],
        order: 1,
        primary_strategy: null,
        auxiliary_strategies: [],
        goal_target_key: "T01I2",
        expected_outcome: "能写出完整过程",
        estimated_minutes: 8,
        required_activity_ids: ["demo-act-1", "demo-act-2"],
        evidence_refs: [],
        dependencies: [],
        student_intro: "今天先来两道小题，不是考试，卡住了也没关系。",
        teacher_reason: "课堂观察发现检验步骤缺失",
      },
      {
        group_id: "demo-group-2",
        goal: "复习去括号的三条规则",
        strategy_types: ["consolidation"],
        activity_ids: ["demo-act-3"],
        order: 2,
        primary_strategy: null,
        auxiliary_strategies: [],
        goal_target_key: "T01I1",
        expected_outcome: "能复述三条规则",
        estimated_minutes: 4,
        required_activity_ids: ["demo-act-3"],
        evidence_refs: [],
        dependencies: [],
        student_intro: "读完这张学习卡就好。",
        teacher_reason: "",
      },
    ],
    dataset_family_id: "demo-family",
    limitations: ["演示方案基于合成数据"],
  },
};

const attempts = new Map<string, AttemptView>();
const attemptGrades = new Map<string, SubmitResponse["grade"]>();
const attemptAnswers = new Map<string, { final_answer: string | null; solution_steps: string | null }>();
let attemptSeq = 0;

const jobs: JobDetail[] = [
  {
    job_id: "demo-job-1",
    kind: "import_process",
    status: "succeeded",
    stage: "done",
    scope: { dataset_id: DATASET_ID, dataset_family_id: null, student_id: null, class_id: null, run_id: RUN_ID },
    created_at: "2026-11-20T07:59:00+00:00",
    updated_at: "2026-11-20T08:00:00+00:00",
    result_ref: RUN_ID,
    error: null,
    warnings: [],
    steps: [
      { step_key: "load", label: "读取数据与课程目录", status: "succeeded", message: null, started_at: null, ended_at: "2026-11-20T07:59:20+00:00" },
      { step_key: "knowledge", label: "构建课程知识体系", status: "succeeded", message: null, started_at: null, ended_at: "2026-11-20T07:59:30+00:00" },
      { step_key: "analysis", label: "构建学情分析", status: "succeeded", message: null, started_at: null, ended_at: "2026-11-20T07:59:40+00:00" },
      { step_key: "plans", label: "构建学习方案", status: "succeeded", message: null, started_at: null, ended_at: "2026-11-20T08:00:00+00:00" },
    ],
    review_status: null,
    publication_status: null,
    plan_version: PLAN_VERSION,
    retry: { root_job_id: "demo-job-1", retry_of_job_id: null, retry_job_id: null, manual_retry_count: 0, can_retry: false, retry_block_reason: "任务未失败" },
    result: { run_id: RUN_ID },
    cancel_requested: false,
  },
];

export function createSession(role: "teacher" | "student", options: { datasetId?: string | null; studentId?: string | null }): SessionInfo {
  return {
    session_token: "demo-session-token",
    expires_at: "2099-01-01T00:00:00+00:00",
    scope: {
      session_id: "demo-session",
      role,
      dataset_id: options.datasetId ?? DATASET_ID,
      dataset_family_id: "demo-family",
      student_id: role === "student" ? options.studentId ?? STUDENT_ID : null,
      allowed_class_ids: classes.map((item) => item.class_id),
      bootstrap: false,
      capabilities: [],
    },
  };
}

export function listDatasets(): DatasetSummary[] {
  return [
    {
      dataset_id: DATASET_ID,
      dataset_family_id: "demo-family",
      status: "ready",
      created_at: "2026-11-20T07:59:00+00:00",
      parent_id: null,
      synthetic: true,
      display_name: "演示数据集",
      student_count: students.length,
      class_count: classes.length,
      catalog_version: CATALOG_VERSION,
      table_row_counts: {},
    },
  ];
}

export function getDemoRoster(datasetId: string): DemoRoster {
  return {
    dataset_id: datasetId,
    classes: classes.map((item) => ({
      class_id: item.class_id,
      student_ids: students.filter((student) => student.class_id === item.class_id).map((student) => student.student_id),
    })),
  };
}

export function listClasses(_datasetId: string): ClassSummary[] {
  return classes;
}

export function listRuns(_datasetId: string): RunSummary[] {
  return [
    {
      run_id: RUN_ID,
      dataset_id: DATASET_ID,
      catalog_version: CATALOG_VERSION,
      knowledge_version: "demo-knowledge-1",
      rule_version: "demo-rules-1",
      status: "succeeded",
      created_at: "2026-11-20T08:00:00+00:00",
      node_ids: [NODE_ID],
    },
  ];
}

export function getClassProfile(_datasetId: string, _classId: string, _nodeId: string | null) {
  return { result: classResult, freshness: "current", limitations: classResult.limitations };
}

export function listStudents(
  _datasetId: string,
  options: { classId?: string | null; page?: number; pageSize?: number } = {},
): Page<RosterStudent> {
  const filtered = options.classId ? students.filter((item) => item.class_id === options.classId) : students;
  return { items: filtered, total: filtered.length, page: 1, page_size: options.pageSize ?? 50 };
}

export function compareClasses(
  _runId: string,
  options: { nodeId: string; leftClassId: string; rightClassId: string },
): ClassComparisonView {
  return {
    class_ids: [options.leftClassId, options.rightClassId],
    node_id: options.nodeId,
    member_analysis_versions: [],
    comparable: false,
    items: [],
    limitations: ["演示数据两班口径不同，不能直接比较差值"],
  };
}

export function listAlerts(_datasetId: string): AlertItem[] {
  return [];
}

export function getStudentProfile(_datasetId: string, _studentId: string, _nodeId: string | null) {
  return { result: assessment, freshness: "current", limitations: [] };
}

export function getPlan(
  _datasetId: string,
  _studentId: string,
  _options: { nodeId?: string | null; planVersion?: string | null } = {},
): PlanView {
  return plan;
}

export function getProgress(
  _datasetId: string,
  _studentId: string,
  options: { page?: number; pageSize?: number } = {},
): Page<ProgressComparison> {
  return { items: progressItems, total: progressItems.length, page: 1, page_size: options.pageSize ?? 20 };
}

export function getAnswerRecords(
  _runId: string,
  _studentId: string,
  options: { nodeId: string; page?: number },
): Page<AnswerRecordListItem> {
  const items: AnswerRecordListItem[] = [
    {
      record_key: "official:R-DEMO-2",
      origin: "official",
      node_id: options.nodeId,
      material_id: "Q3",
      record_id: "R-DEMO-2",
      attempt_id: null,
      grade_id: null,
      grading_status: "graded",
      score: 3,
      max_score: 5,
      occurred_at: "2026-09-10T09:10:00+00:00",
      evidence_ref: { kind: "record", id: "R-DEMO-2", run_id: RUN_ID },
    },
  ];
  return { items, total: items.length, page: 1, page_size: 20 };
}

export function listLearningEvents(
  _datasetId: string,
  _studentId: string,
  options: { page?: number; pageSize?: number } = {},
): Page<LearningEventView> {
  return { items: [], total: 0, page: 1, page_size: options.pageSize ?? 200 };
}

export function getCatalog(): Catalog {
  return {
    catalog_version: CATALOG_VERSION,
    knowledge_version: "demo-knowledge-1",
    topics: [
      { topic_id: "T01", title: "一元一次方程", goal: "能去括号并解一元一次方程" },
      { topic_id: "T02", title: "一元一次不等式", goal: "能在数轴上表示解集" },
    ],
    indicators: [
      { indicator_id: "T01I1", topic_id: "T01", description: indicatorNames.T01I1, curriculum_ref: "KB-T01-01", curriculum_page: "标准第 12 页", textbook_pages: "B01 第 8-12 页" },
      { indicator_id: "T01I2", topic_id: "T01", description: indicatorNames.T01I2, curriculum_ref: "KB-T01-02", curriculum_page: "标准第 12 页", textbook_pages: "B01 第 9-13 页" },
      { indicator_id: "T02I1", topic_id: "T02", description: indicatorNames.T02I1, curriculum_ref: "KB-T02-01", curriculum_page: "标准第 15 页", textbook_pages: "B02 第 5-9 页" },
    ],
    materials: Object.values(materials),
    links: [{ source_id: "T01I1", target_id: "T01I2", relation: "supports", basis: "课标顺序", description: "符号处理支撑解方程步骤" }],
  };
}

export function getKnowledge(): KnowledgeSnapshotView {
  const points = Object.entries(indicatorNames).map(([kpId, name], index) => ({
    kp_id: `kp-${kpId}`,
    name,
    aliases: [],
    kind: "concept",
    definition: name,
    scope_role: "core",
    topic_ids: [kpId.startsWith("T01") ? "T01" : "T02"],
    indicator_ids: [kpId],
    snippet: name,
    source_locators: [{ source_file: "演示课程要求.csv", verification_status: "unverified" }],
    review_info: { status: "validated", method: "rule" },
    content_hash: `demo-${index}`,
  }));
  return {
    dataset_id: DATASET_ID,
    catalog_version: CATALOG_VERSION,
    knowledge_version: "demo-knowledge-1",
    parent_version: null,
    status: "published",
    points,
    relations: [{ source_ids: ["kp-T01I1"], target_id: "kp-T01I2", relation_type: "prerequisite", basis: "课标顺序" }],
    annotations: [],
    created_at: "2026-11-20T07:59:30+00:00",
    review_summary: { counts: { formal_points: 3, formal_relations: 1, formal_annotations: 0 } },
    limitations: ["演示知识体系为合成数据"],
  };
}

export function listKnowledgeReviews(): Page<KnowledgeReviewItem> {
  return { items: [], total: 0, page: 1, page_size: 50 };
}

export function getKnowledgeQuality(): KnowledgeQualityReport {
  return {
    dataset_id: DATASET_ID,
    catalog_version: CATALOG_VERSION,
    knowledge_version: "demo-knowledge-1",
    candidate_version: "demo-knowledge-1",
    generated_at: "2026-11-20T07:59:30+00:00",
    counts: { formal_points: 3, formal_relations: 1, formal_annotations: 0 },
    coverage: [],
    issues: [],
    limitations: ["演示知识体系为合成数据"],
  };
}

export function listQuality(_datasetId: string): Page<QualityIssue> {
  return {
    items: [
      {
        issue_id: "QI-DEMO-1",
        code: "SUPERSEDED_REVISION",
        severity: "info",
        record_ids: ["R01"],
        event_id: "E01",
        message: "演示质量问题：事件存在已修订版本，按最高版本保留",
        action: "retain",
        rule_version: "demo-rules",
      },
    ],
    total: 1,
    page: 1,
    page_size: 100,
  };
}

export function getAnalysisQuality(): Record<string, unknown> {
  return {
    node_id: NODE_ID,
    generated_at: "2026-11-20T08:00:00+00:00",
    rule_version: "demo-rules-1",
    counts: { status_graded: 2, status_pending: 1 },
    coverage: [],
    issues: listQuality(DATASET_ID).items,
    limitations: ["演示质量计数来自合成数据"],
  };
}

export function listJobs(_datasetId: string): Page<JobDetail> {
  return { items: jobs, total: jobs.length, page: 1, page_size: 20 };
}

export function getJob(jobId: string): JobDetail {
  return jobs.find((job) => job.job_id === jobId) ?? jobs[0];
}

export function getJobEvents(jobId: string, afterSequence = 0): JobEventPage {
  const all = [
    { sequence: 1, stage: "queued", status: "queued", message: "任务已入队", occurred_at: "2026-11-20T07:59:00+00:00" },
    { sequence: 2, stage: "load", status: "succeeded", message: "读取完成", occurred_at: "2026-11-20T07:59:20+00:00" },
    { sequence: 3, stage: "done", status: "succeeded", message: "任务完成", occurred_at: "2026-11-20T08:00:00+00:00" },
  ].filter((event) => event.sequence > afterSequence);
  void jobId;
  return { items: all, next_sequence: all.at(-1)?.sequence ?? afterSequence, has_more: false };
}

export function cancelJob(jobId: string): JobDetail {
  return getJob(jobId);
}

export function retryJob(jobId: string): JobDetail {
  return getJob(jobId);
}

export function resolveEvidence(ref: EvidenceRef): EvidenceDetail {
  const base: EvidenceDetail = {
    ref,
    title: "演示证据",
    detail_type: ref.kind === "metric" ? "metric" : ref.kind === "curriculum" ? "curriculum" : ref.kind === "derived" ? "derived" : "record",
    detail:
      ref.kind === "metric"
        ? {
            metric: studentMetrics[1],
            calculation_description: "同口径有效记录得分率",
            evidence_count: 2,
            record_refs: [{ kind: "record", id: "R-DEMO-2", run_id: RUN_ID }],
            excluded_count: 0,
            limitations: [],
          }
        : {
            question: materials.Q3,
            student_id: STUDENT_ID,
            status: "graded",
            score: 3,
            max_score: 5,
            final_answer: "x=5",
            solution_steps: "去括号得 2x-5=5，解得 x=5。",
            rubric_scores: [{ point_id: "P1", description: "去括号正确", score: 2, max_score: 2 }],
            quality_issues: [],
            related_findings: [],
          },
    summary: "演示数据，仅用于界面检查。",
    related_refs: [],
    limitations: [],
    visibility: "full",
  };
  return base;
}

export function getMaterial(materialId: string): MaterialView {
  const material = materials[materialId];
  if (!material) throw new Error("演示数据里没有这道材料。");
  return material;
}

export function startActivity(planVersion: string, activityId: string): StartActivityResponse {
  const item = activities.find((entry) => entry.activity_id === activityId);
  if (!item) throw new Error("演示方案里没有这个活动。");
  if (item.activity_kind !== "question") return { activity_id: activityId, state: "in_progress", event_id: "demo-event-start" };
  const attempt = createAttempt(planVersion, activityId);
  return { activity_id: activityId, state: "in_progress", event_id: null, attempt };
}

export function createAttempt(planVersion: string, activityId: string): AttemptView {
  attemptSeq += 1;
  const attempt: AttemptView = {
    attempt_id: `demo-attempt-${attemptSeq}`,
    student_id: STUDENT_ID,
    plan_version: planVersion,
    activity_id: activityId,
    material_id: activities.find((item) => item.activity_id === activityId)?.material_id ?? "",
    status: "in_progress",
    submitted_at: null,
    final_answer: null,
    solution_steps: null,
    assistance_status: "independent",
    hint_max_level: 0,
    agent_help_count: 0,
    submission_no: 0,
    latest_grade_id: null,
  };
  attempts.set(attempt.attempt_id, attempt);
  return attempt;
}

export function getAttempt(attemptId: string): AttemptResponse {
  const attempt = attempts.get(attemptId);
  if (!attempt) throw new Error("演示数据里没有这次作答。");
  const answers = attemptAnswers.get(attemptId);
  return {
    attempt: { ...attempt, ...(answers ?? {}) },
    grade: attemptGrades.get(attemptId) ?? null,
    feedback: null,
    group_status: null,
    update_job_id: null,
  };
}

export function requestHint(attemptId: string, level: number, _allowAssisted: boolean): HintResponse {
  const attempt = attempts.get(attemptId);
  if (!attempt) throw new Error("演示数据里没有这次作答。");
  const item = activities.find((entry) => entry.activity_id === attempt.activity_id);
  const hint = item?.hint_levels[level - 1];
  if (!hint) throw new Error("该题没有这一级提示。");
  attempt.hint_max_level = Math.max(attempt.hint_max_level ?? 0, level);
  attempt.assistance_status = "assisted";
  return { event_id: `demo-hint-${level}`, level, hint };
}

export function submitAttempt(
  attemptId: string,
  payload: { final_answer?: string | null; solution_steps?: string | null; unanswered: boolean },
): SubmitResponse {
  const attempt = attempts.get(attemptId);
  if (!attempt) throw new Error("演示数据里没有这次作答。");
  const material = materials[attempt.material_id];
  const finalAnswer = payload.unanswered ? null : payload.final_answer?.trim() || null;
  const steps = payload.unanswered ? null : payload.solution_steps?.trim() || null;
  attemptAnswers.set(attemptId, { final_answer: finalAnswer, solution_steps: steps });
  attempt.status = "submitted";
  attempt.submitted_at = new Date().toISOString();
  attempt.submission_no += 1;

  let grade: SubmitResponse["grade"] = null;
  if (material.question_type === "choice" || material.question_type === "fill_blank") {
    const expected = material.material_id === "Q1" ? "B" : "4";
    const correct = (finalAnswer ?? "").replace(/\s/g, "").toUpperCase() === expected || (finalAnswer ?? "").includes(expected);
    grade = {
      grade_id: `demo-grade-${attemptId}`,
      attempt_id: attemptId,
      revision: 1,
      status: "graded",
      validation_status: "validated",
      score: correct ? material.max_score : 0,
      max_score: material.max_score,
      rubric_scores: [],
      method: "rule",
      limitations: [],
      created_at: new Date().toISOString(),
    };
    attempt.status = "completed";
  } else {
    grade = {
      grade_id: `demo-grade-${attemptId}`,
      attempt_id: attemptId,
      revision: 1,
      status: "needs_review",
      validation_status: "pending",
      score: null,
      max_score: material.max_score,
      rubric_scores: [],
      method: "rule",
      limitations: ["解答题需要按评分点复核"],
      created_at: new Date().toISOString(),
    };
  }
  attemptGrades.set(attemptId, grade);
  return { attempt: { ...attempt }, grade, job_id: null, feedback_status: grade.status, group_status: null };
}

export function completeActivity(planVersion: string, activityId: string): CompleteActivityResponse {
  void planVersion;
  const item = activities.find((entry) => entry.activity_id === activityId);
  if (item) item.state = "completed";
  return { activity_id: activityId, state: "completed", event_id: "demo-event-complete", group_completed: false };
}

export function getModelSettings(): ModelSettings {
  return { configured: false, provider: null, url: null, model_id: null, has_api_key: false };
}

export function saveModelSettings(): ModelSettings {
  return getModelSettings();
}

export function testModelSettings(): { connected: boolean; message: string } {
  return { connected: false, message: "演示模式不会连接真实模型服务。" };
}

export function sendChat(): { text: string; tool_trace: never[] } {
  return { text: "", tool_trace: [] };
}

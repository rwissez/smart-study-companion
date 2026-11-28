/** 展示用人类可读文案。内部编号、状态枚举与接口路径不直接出现在页面上。 */

export const UNCLASSIFIED_TOPIC_LABEL = "未归类";

export function relationTypeLabel(type: string | null | undefined): string {
  switch ((type ?? "").toLowerCase()) {
    case "prerequisite":
      return "前置";
    case "special_case":
      return "特例";
    case "composed_of":
      return "构成";
    case "derives_from":
      return "来源";
    case "related":
      return "关联";
    default:
      return "关联";
  }
}

export function knowledgeScopeLabel(scope: string | null | undefined): string {
  return scope === "core" ? "核心概念" : "拓展概念";
}

export function datasetStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case "ready":
      return "数据可用";
    case "ready_with_issues":
      return "可用，含质量问题";
    case "blocked":
      return "不可用";
    case "processing":
      return "处理中";
    default:
      return status ? "状态待确认" : "状态未知";
  }
}

export function jobStatusText(status: string | null | undefined): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "执行中";
    case "succeeded":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return status ? "状态待确认" : "状态未知";
  }
}

export function jobStageLabel(stage: string | null | undefined): string {
  switch (stage) {
    case "queued":
      return "排队";
    case "load":
      return "读取数据";
    case "knowledge":
      return "构建知识体系";
    case "analysis":
      return "学情分析";
    case "plans":
      return "生成学习方案";
    case "done":
      return "全部完成";
    default:
      return stage ? "处理中" : "未开始";
  }
}

export function jobKindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case "import_process":
      return "导入与分析";
    case "student_update":
      return "学情更新";
    case "lesson_regenerate":
      return "微课堂重生成";
    case "export":
      return "成果导出";
    default:
      return "后台任务";
  }
}

export function severityLabel(severity: string | null | undefined): string {
  switch (severity) {
    case "info":
      return "提示";
    case "warning":
      return "警告";
    case "error":
      return "错误";
    default:
      return "提示";
  }
}

export function qualityCodeLabel(code: string | null | undefined): string {
  switch (code) {
    case "SUPERSEDED_REVISION":
      return "记录存在修订版本";
    case "MISSING_FIELD":
      return "字段缺失";
    case "DUPLICATE_RECORD":
      return "重复记录";
    case "UNKNOWN_STUDENT":
      return "学生未登记";
    case "UNKNOWN_NODE":
      return "节点未登记";
    case "UNGRADED_EVENT":
      return "未评分记录";
    case "LOW_COVERAGE":
      return "覆盖不足";
    default:
      return "质量问题";
  }
}

export function qualityActionLabel(action: string | null | undefined): string {
  switch (action) {
    case "retain":
      return "保留记录";
    case "exclude_score":
      return "不计入得分";
    case "quarantine_event":
      return "隔离事件";
    case "block_import":
      return "阻止导入";
    default:
      return "按规则处理";
  }
}

export function datasetStateLabel(state: string | null | undefined): string {
  switch (state) {
    case "ready":
      return "就绪";
    case "processing":
      return "处理中";
    case "failed":
      return "处理失败";
    default:
      return state ? "状态待确认" : "未提供";
  }
}

const TABLE_LABELS: Record<string, string> = {
  requirements: "课程要求",
  materials: "材料与题目",
  students: "学生名册",
  nodes: "测验节点",
  records: "作答记录",
  dictionary: "字典表",
};

export function tableLabel(table: string): string {
  return TABLE_LABELS[table] ?? "数据表";
}

export function knowledgeCountLabel(key: string): string | null {
  const labels: Record<string, string> = {
    source_files: "来源文件",
    total_pages: "总页数",
    relevant_pages: "相关页数",
    usable_relevant_pages: "可用相关页",
    recovered_pages: "修复页数",
    formal_points: "正式知识点",
    formal_relations: "正式关系",
    formal_annotations: "材料关联",
    unresolved_items: "未解决项",
  };
  return labels[key] ?? null;
}

export function countLabel(key: string): string | null {
  const labels: Record<string, string> = {
    status_graded: "已评分记录",
    status_pending: "待评分记录",
    status_unanswered: "未作答",
    status_unknown: "状态未知",
    status_needs_review: "待复核",
    status_submitted: "已提交",
  };
  return labels[key] ?? null;
}

/** 节点没有名字接口，用顺序号代替内部编号。 */
export function nodeLabel(nodeId: string | null | undefined, nodeIds: string[]): string {
  if (!nodeId) return "未选择节点";
  const index = nodeIds.indexOf(nodeId);
  return index >= 0 ? `第 ${index + 1} 次观察` : "当前观察节点";
}

export function exportStatusLabel(status: string): string {
  switch (status) {
    case "ready":
      return "可下载";
    case "queued":
      return "排队中";
    case "running":
      return "生成中";
    case "failed":
      return "生成失败";
    default:
      return "状态待确认";
  }
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function metricNameLabel(name: string | null | undefined): string {
  switch (name) {
    case "valid_score_ratio":
      return "有效得分率";
    case "full_score_ratio":
      return "满分率";
    case "observed_response_ratio":
      return "有作答比例";
    case "item_coverage":
      return "题目覆盖";
    case "student_observation_rate":
      return "学生观测覆盖率";
    default:
      return "观察指标";
  }
}

export function unitLabel(unit: string | null | undefined): string {
  switch (unit) {
    case "ratio":
      return "比例";
    case "count":
      return "计数";
    case "score":
      return "得分";
    default:
      return "指标";
  }
}

export function recommendationQualityCountLabel(key: string): string | null {
  const labels: Record<string, string> = {
    plans_generated: "生成方案",
    plans_published: "已发布方案",
    plans_human_confirmed: "教师确认发布",
    plans_needs_review: "方案待审核",
    plans_kept_applicable: "复查后仍适用",
    material_shortage: "材料不足",
    strategy_trials: "策略试验",
    interventions_opened: "新开介入",
    interventions_resolved: "已结案介入",
    interventions_open_at_end: "期末未结案介入",
    activities_paused: "暂停活动",
    rule_degraded: "规则降级",
    jobs_failed: "失败任务",
  };
  return labels[key] ?? null;
}

export function issueCodeLabel(code: string): string {
  if (!code) return "质量提示";
  return "质量检查项";
}

const METRIC_LABELS: Record<string, string> = {
  valid_score_ratio: "得分率",
  full_score_ratio: "满分题比例",
  observed_response_ratio: "有作答比例",
  item_coverage: "题库覆盖率",
  student_observation_rate: "学生观测覆盖率",
};

/** 依据引用只带 metric_id 时，从 id 尾部解析指标名，避免统一显示成“指标”。 */
export function evidenceRefLabel(kind: string, id: string): string {
  if (kind === "record") return "作答记录";
  if (kind === "curriculum") return "课程知识";
  if (kind === "material") return "学习材料";
  if (kind === "derived" || kind === "finding") return "诊断依据";
  if (kind === "learning_event") return "学习事件";
  if (kind === "metric") {
    const matched = Object.keys(METRIC_LABELS).find((key) => id.endsWith(key));
    return matched ? METRIC_LABELS[matched] : "指标";
  }
  return "依据";
}



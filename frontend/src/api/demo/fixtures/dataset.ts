import type {
  ClassSummary,
  DatasetSummary,
  RosterStudent,
  RunSummary,
} from "../../types";

/** 示例数据集与运行：固定已就绪，用于页面预览 */
export const demoDataset: DatasetSummary = {
  dataset_id: "DEMO-DATA",
  created_at: "2026-11-20T10:00:00+08:00",
  status: "ready_with_issues",
  student_count: 4,
  record_count: 7,
};

export const demoRun: RunSummary = {
  run_id: "DEMO-RUN",
  dataset_id: "DEMO-DATA",
  catalog_version: "DEMO-CATALOG",
  status: "succeeded",
  node_ids: ["DEMO-N1", "DEMO-N2"],
  created_at: "2026-11-20T10:05:00+08:00",
  message: null,
};

export interface DemoNode {
  node_id: string;
  name: string;
  teaching_scope: string;
}

/** 两个节点的教学范围不同，页面并列查阅，不做跨节点进退步比较 */
export const demoNodes: DemoNode[] = [
  {
    node_id: "DEMO-N1",
    name: "第一次测验（一元一次方程）",
    teaching_scope: "只测到一元一次方程，方程组与不等式尚未教学",
  },
  {
    node_id: "DEMO-N2",
    name: "第二次测验（加入不等式）",
    teaching_scope: "加入方程组与不等式，范围与第一次不同",
  },
];

export const demoClasses: ClassSummary[] = [
  { class_id: "DEMO-A", roster_count: 2 },
  { class_id: "DEMO-B", roster_count: 2 },
];

export const demoClassNames: Record<string, string> = {
  "DEMO-A": "甲班",
  "DEMO-B": "乙班",
};

export const demoRoster: RosterStudent[] = [
  { student_id: "DEMO-S1", class_id: "DEMO-A", grade: 7, observed_event_count: 1 },
  { student_id: "DEMO-S2", class_id: "DEMO-A", grade: 7, observed_event_count: 2 },
  { student_id: "DEMO-S3", class_id: "DEMO-B", grade: 7, observed_event_count: 0 },
  { student_id: "DEMO-S4", class_id: "DEMO-B", grade: 7, observed_event_count: 1 },
];

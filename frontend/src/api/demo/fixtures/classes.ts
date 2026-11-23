import type { ClassResult } from "../../types";

const base = {
  dataset_id: "DEMO-DATA",
  run_id: "DEMO-RUN",
  catalog_version: "DEMO-CATALOG",
};

/**
 * 示例班级结果：两个班在 N1 使用同一口径，可直接并列；
 * N2 缺少可比数据，页面显示为暂不可用，不做跨节点比较。
 */
export const demoClassResults: Record<string, Record<string, ClassResult>> = {
  "DEMO-A": {
    "DEMO-N1": {
      ...base,
      class_id: "DEMO-A",
      node_id: "DEMO-N1",
      roster_count: 2,
      observed_student_count: 2,
      metrics: [
        {
          metric_id: "DEMO-CA-RATIO",
          name: "得分率",
          unit: "ratio",
          value: 0.6666666667,
          numerator: 6,
          denominator: 9,
          scope: { class_id: "DEMO-A", node_id: "DEMO-N1", indicator_id: "DEMO-I1" },
          evidence_refs: [
            { kind: "record", id: "DEMO-R1", run_id: "DEMO-RUN" },
            { kind: "record", id: "DEMO-R2a", run_id: "DEMO-RUN" },
            { kind: "record", id: "DEMO-R2b", run_id: "DEMO-RUN" },
          ],
          excluded_event_count: 0,
          limitations: ["按汇总得分除以汇总满分计算，未按人数平均"],
        },
      ],
      findings: [
        {
          finding_id: "DEMO-CAF1",
          indicator_id: "DEMO-I1",
          description:
            "三条示例作答中有正确的等式变形，也有求解后未写检验的记录。",
          performance: "needs_practice",
          evidence_status: "sufficient",
          evidence_refs: [
            { kind: "metric", id: "DEMO-CA-RATIO", run_id: "DEMO-RUN" },
          ],
          limitations: ["样本只有两人，共性问题只能说明本班情况"],
        },
      ],
      limitations: ["仅用于页面预览"],
    },
    "DEMO-N2": {
      ...base,
      class_id: "DEMO-A",
      node_id: "DEMO-N2",
      roster_count: 2,
      observed_student_count: 0,
      metrics: [],
      findings: [],
      limitations: ["该节点只有待评分记录，暂不产生班级统计"],
    },
  },

  "DEMO-B": {
    "DEMO-N1": {
      ...base,
      class_id: "DEMO-B",
      node_id: "DEMO-N1",
      roster_count: 2,
      observed_student_count: 1,
      metrics: [
        {
          metric_id: "DEMO-CB-RATIO",
          name: "得分率",
          unit: "ratio",
          value: 0,
          numerator: 0,
          denominator: 3,
          scope: { class_id: "DEMO-B", node_id: "DEMO-N1", indicator_id: "DEMO-I1" },
          evidence_refs: [{ kind: "record", id: "DEMO-R6Z", run_id: "DEMO-RUN" }],
          excluded_event_count: 0,
          limitations: ["名册 2 人，仅 1 人有有效记录，不能代表整班"],
        },
      ],
      findings: [
        {
          finding_id: "DEMO-CBF1",
          indicator_id: "DEMO-I1",
          description:
            "名册两名学生中只有一人留下有效记录，另一人无记录，群体结论证据不足。",
          performance: "undetermined",
          evidence_status: "insufficient",
          evidence_refs: [{ kind: "metric", id: "DEMO-CB-RATIO", run_id: "DEMO-RUN" }],
          limitations: ["观测人数与名册人数不同，不能当作全班掌握情况"],
        },
      ],
      limitations: ["仅用于页面预览"],
    },
    "DEMO-N2": {
      ...base,
      class_id: "DEMO-B",
      node_id: "DEMO-N2",
      roster_count: 2,
      observed_student_count: 0,
      metrics: [],
      findings: [
        {
          finding_id: "DEMO-CBF2",
          indicator_id: "DEMO-I1",
          description: "该节点一条记录被隔离、一条未作答，没有可用统计值。",
          performance: "undetermined",
          evidence_status: "conflicting",
          evidence_refs: [{ kind: "record", id: "DEMO-R5Q", run_id: "DEMO-RUN" }],
          limitations: ["隔离记录保留原文，等待复核"],
        },
      ],
      limitations: ["该节点无可比数据"],
    },
  },
};

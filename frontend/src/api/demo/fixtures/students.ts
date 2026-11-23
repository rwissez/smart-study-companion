import type { StudentResult } from "../../types";

const base = {
  dataset_id: "DEMO-DATA",
  run_id: "DEMO-RUN",
  catalog_version: "DEMO-CATALOG",
};

/**
 * 示例学生结果：每个学生每个节点一份。
 * 覆盖部分得分、表现较好、无记录、有效零分、待评分、未作答、隔离七种情形。
 */
export const demoStudentResults: Record<string, Record<string, StudentResult>> = {
  "DEMO-S1": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S1",
      class_id: "DEMO-A",
      node_id: "DEMO-N1",
      metrics: [
        {
          metric_id: "DEMO-S1-RATIO",
          name: "得分率",
          unit: "ratio",
          value: 0.3333333333,
          numerator: 1,
          denominator: 3,
          scope: {
            student_id: "DEMO-S1",
            node_id: "DEMO-N1",
            indicator_id: "DEMO-I1",
          },
          evidence_refs: [{ kind: "record", id: "DEMO-R1", run_id: "DEMO-RUN" }],
          excluded_event_count: 0,
          limitations: ["仅覆盖一条示例题记录"],
        },
      ],
      findings: [
        {
          finding_id: "DEMO-F1",
          indicator_id: "DEMO-I1",
          description:
            "本题正确移项并化简到 2x = 12，写出的解不满足该等式，且未见代入检验。单题证据不足以判断问题是否反复出现。",
          performance: "undetermined",
          evidence_status: "insufficient",
          evidence_refs: [{ kind: "record", id: "DEMO-R1", run_id: "DEMO-RUN" }],
          limitations: ["观察范围只包括单题"],
        },
      ],
      limitations: ["仅用于页面预览"],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S1",
      class_id: "DEMO-A",
      node_id: "DEMO-N2",
      metrics: [],
      findings: [
        {
          finding_id: "DEMO-F1N2",
          indicator_id: "DEMO-I1",
          description: "该节点没有这名学生的记录，不能据此判断掌握情况。",
          performance: "undetermined",
          evidence_status: "insufficient",
          evidence_refs: [],
          limitations: ["无记录不等于未掌握"],
        },
      ],
      limitations: ["该节点暂无记录"],
    },
  },

  "DEMO-S2": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S2",
      class_id: "DEMO-A",
      node_id: "DEMO-N1",
      metrics: [
        {
          metric_id: "DEMO-S2-RATIO",
          name: "得分率",
          unit: "ratio",
          value: 0.8333333333,
          numerator: 5,
          denominator: 6,
          scope: {
            student_id: "DEMO-S2",
            node_id: "DEMO-N1",
            indicator_id: "DEMO-I1",
          },
          evidence_refs: [
            { kind: "record", id: "DEMO-R2a", run_id: "DEMO-RUN" },
            { kind: "record", id: "DEMO-R2b", run_id: "DEMO-RUN" },
          ],
          excluded_event_count: 0,
          limitations: ["两道题均为解答题，过程完整"],
        },
      ],
      findings: [
        {
          finding_id: "DEMO-F2",
          indicator_id: "DEMO-I1",
          description:
            "两道题中都观察到正确的等式变形；其中一道写出代入检验，另一道没有。",
          performance: "observed_strength",
          evidence_status: "sufficient",
          evidence_refs: [
            { kind: "record", id: "DEMO-R2a", run_id: "DEMO-RUN" },
            { kind: "record", id: "DEMO-R2b", run_id: "DEMO-RUN" },
          ],
          limitations: ["证据来自同一次测验，不含长期观察"],
        },
      ],
      limitations: ["仅用于页面预览"],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S2",
      class_id: "DEMO-A",
      node_id: "DEMO-N2",
      metrics: [],
      findings: [],
      limitations: ["该节点当前只有一条待评分记录，暂不产生统计"],
    },
  },

  "DEMO-S3": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S3",
      class_id: "DEMO-B",
      node_id: "DEMO-N1",
      metrics: [],
      findings: [
        {
          finding_id: "DEMO-F3",
          indicator_id: "DEMO-I1",
          description: "这名学生在本次测验中没有作答记录，暂时无法判断表现。",
          performance: "undetermined",
          evidence_status: "insufficient",
          evidence_refs: [],
          limitations: ["无记录不等于未掌握，需要补充观察"],
        },
      ],
      limitations: ["该节点无记录"],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S3",
      class_id: "DEMO-B",
      node_id: "DEMO-N2",
      metrics: [],
      findings: [
        {
          finding_id: "DEMO-F3N2",
          indicator_id: "DEMO-I1",
          description: "该节点有未作答记录，未作答不能当作答错处理。",
          performance: "undetermined",
          evidence_status: "insufficient",
          evidence_refs: [{ kind: "record", id: "DEMO-R4U", run_id: "DEMO-RUN" }],
          limitations: ["未作答记录不计入得分率"],
        },
      ],
      limitations: ["该节点为未作答"],
    },
  },

  "DEMO-S4": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S4",
      class_id: "DEMO-B",
      node_id: "DEMO-N1",
      metrics: [
        {
          metric_id: "DEMO-S4-RATIO",
          name: "得分率",
          unit: "ratio",
          value: 0,
          numerator: 0,
          denominator: 3,
          scope: {
            student_id: "DEMO-S4",
            node_id: "DEMO-N1",
            indicator_id: "DEMO-I1",
          },
          evidence_refs: [{ kind: "record", id: "DEMO-R6Z", run_id: "DEMO-RUN" }],
          excluded_event_count: 0,
          limitations: ["这是有效的 0 分，与无记录不同"],
        },
      ],
      findings: [
        {
          finding_id: "DEMO-F4",
          indicator_id: "DEMO-I1",
          description: "三个评分点均为零分。作答写出 2x = 2，与原方程不等价，尚不能确定哪项操作导致错误。",
          performance: "needs_practice",
          evidence_status: "insufficient",
          evidence_refs: [{ kind: "record", id: "DEMO-R6Z", run_id: "DEMO-RUN" }],
          limitations: ["只有一条有效记录，不能外推为掌握程度"],
        },
      ],
      limitations: ["仅用于页面预览"],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S4",
      class_id: "DEMO-B",
      node_id: "DEMO-N2",
      metrics: [],
      findings: [
        {
          finding_id: "DEMO-F4N2",
          indicator_id: "DEMO-I1",
          description: "该节点有一条记录因评分异常被隔离，未计入统计。",
          performance: "undetermined",
          evidence_status: "conflicting",
          evidence_refs: [{ kind: "record", id: "DEMO-R5Q", run_id: "DEMO-RUN" }],
          limitations: ["隔离记录保留原文，等待复核"],
        },
      ],
      limitations: ["该节点记录被隔离"],
    },
  },
};

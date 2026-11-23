import type { RecommendationBundle } from "../../types";

const base = {
  dataset_id: "DEMO-DATA",
  run_id: "DEMO-RUN",
  catalog_version: "DEMO-CATALOG",
  validation_issues: [] as Array<{ code: string; message: string }>,
};

/** 示例建议：四名学生对应四种不同的学习需求 */
export const demoRecommendations: Record<string, Record<string, RecommendationBundle>> = {
  "DEMO-S1": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S1",
      node_id: "DEMO-N1",
      recommendations: [
        {
          recommendation_id: "DEMO-REC1",
          indicator_id: "DEMO-I1",
          finding_ids: ["DEMO-F1"],
          goal: "复核等式两边同除，并用代入检查答案",
          reason: "本题移项正确，但写出的解不满足化简后的等式，且未见代入检验。",
          activities: [
            {
              order: 1,
              material_id: "DEMO-M1",
              purpose: "复习等式两边同除和代入检验",
              hint_levels: [
                "要让未知数的系数变为 1，等式两边需要同时做什么？",
                "把所得的数代回原方程，分别算等号两边。",
              ],
              observation_points: ["能说出两边做了同样的操作"],
              previously_attempted: false,
            },
            {
              order: 2,
              material_id: "DEMO-M2",
              purpose: "重做同一道题，重点写检验",
              hint_levels: ["从化简后的等式出发，写出两边同除的过程。"],
              observation_points: ["等号两边是否除以同一个非零数", "是否写出检验步骤"],
              previously_attempted: true,
            },
          ],
          adjustment_conditions: [
            {
              observable_condition: "能说明两边同除并验证所得的解",
              next_action: "转入变式题 DEMO-M3，减少提示",
            },
            {
              observable_condition: "求出的值代入后两边仍不相等",
              next_action: "回到学习卡 DEMO-M1，配合教师逐步示范",
            },
          ],
          review_status: "validated",
          generation_method: "rule",
        },
      ],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S1",
      node_id: "DEMO-N2",
      recommendations: [],
    },
  },

  "DEMO-S2": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S2",
      node_id: "DEMO-N1",
      recommendations: [
        {
          recommendation_id: "DEMO-REC2",
          indicator_id: "DEMO-I1",
          finding_ids: ["DEMO-F2"],
          goal: "把已经会做的移项迁移到变式题",
          reason: "两题均有正确变形，其中一道写出了检验。用新题练迁移，同时补强验算。",
          activities: [
            {
              order: 1,
              material_id: "DEMO-M5",
              purpose: "去括号与移项组合的变式练习",
              hint_levels: ["先处理括号，再移项。"],
              observation_points: ["去括号时是否变号", "移项与合并是否一次完成"],
              previously_attempted: false,
            },
          ],
          adjustment_conditions: [
            {
              observable_condition: "变式题也拿到满分",
              next_action: "进入实际问题建模，使用指标 DEMO-I2 的练习",
            },
          ],
          review_status: "validated",
          generation_method: "rule",
        },
      ],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S2",
      node_id: "DEMO-N2",
      recommendations: [],
    },
  },

  "DEMO-S3": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S3",
      node_id: "DEMO-N1",
      recommendations: [
        {
          recommendation_id: "DEMO-REC3",
          indicator_id: "DEMO-I1",
          finding_ids: ["DEMO-F3"],
          goal: "先补一次观察，再决定练什么",
          reason: "没有作答记录，不能判断会或不会，先用一道小题观察。",
          activities: [
            {
              order: 1,
              material_id: "DEMO-M4",
              purpose: "跟着教材例题看一遍完整解法",
              hint_levels: ["先看例题第一步做了什么。"],
              observation_points: ["能否复述例题的第一步"],
              previously_attempted: false,
            },
            {
              order: 2,
              material_id: "DEMO-M2",
              purpose: "独立完成一道同类型题，用于补充观察",
              hint_levels: ["先写移项后的等式。"],
              observation_points: ["是否动手写出过程", "卡在哪一步"],
              previously_attempted: false,
            },
          ],
          adjustment_conditions: [
            {
              observable_condition: "补充练习完成后有完整过程",
              next_action: "按实际得分重新生成本节点分析",
            },
          ],
          review_status: "needs_review",
          generation_method: "rule",
        },
      ],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S3",
      node_id: "DEMO-N2",
      recommendations: [],
    },
  },

  "DEMO-S4": {
    "DEMO-N1": {
      ...base,
      student_id: "DEMO-S4",
      node_id: "DEMO-N1",
      recommendations: [
        {
          recommendation_id: "DEMO-REC4",
          indicator_id: "DEMO-I1",
          finding_ids: ["DEMO-F4"],
          goal: "核对移项后的等式",
          reason: "移项后的等式与原式不等价，三个评分点均为零分，具体原因需再观察。",
          activities: [
            {
              order: 1,
              material_id: "DEMO-M1",
              purpose: "查阅等式性质并核对等号两边的操作",
              hint_levels: ["移项时，这一项的符号要变成相反数。"],
              observation_points: ["能否指出哪一项需要变号"],
              previously_attempted: false,
            },
            {
              order: 2,
              material_id: "DEMO-M2",
              purpose: "重做同一道题，只要求写出移项后的等式",
              hint_levels: ["两边同时减去 x 之后，右边还剩什么？"],
              observation_points: ["移项后符号是否正确"],
              previously_attempted: true,
            },
          ],
          adjustment_conditions: [
            {
              observable_condition: "移项后的等式连续两次正确",
              next_action: "继续完成合并同类项与检验",
            },
          ],
          review_status: "validated",
          generation_method: "rule",
        },
      ],
    },
    "DEMO-N2": {
      ...base,
      student_id: "DEMO-S4",
      node_id: "DEMO-N2",
      recommendations: [],
    },
  },
};

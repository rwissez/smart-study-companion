import type { Catalog } from "../../types";

/**
 * 示例课程目录。编号统一加 DEMO 前缀，避免与官方材料编号混淆；
 * 主题名与观察指标描述沿用课标口径，便于演示时对照真实教学用语。
 */
export const demoCatalog: Catalog = {
  catalog_version: "DEMO-CATALOG",
  topics: [
    {
      topic_id: "DEMO-T1",
      name: "代数式与整式运算",
      goal: "能用代数式表示简单数量关系、代入求值，并进行整式加减与解释。",
    },
    {
      topic_id: "DEMO-T2",
      name: "方程与方程组",
      goal: "能解一元一次方程和二元一次方程组，依据等量关系建立模型并检验答案。",
    },
    {
      topic_id: "DEMO-T3",
      name: "不等式与不等式组",
      goal: "能解一元一次不等式及简单不等式组，表示解集，并解决范围约束问题。",
    },
  ],
  indicators: [
    {
      indicator_id: "DEMO-I1",
      topic_id: "DEMO-T2",
      description: "运用等式性质解一元一次方程",
      curriculum_ref: "DEMO-CR02",
      curriculum_page: "合成示例，无官方页码",
      textbook_pages: "合成示例，无官方页码",
    },
    {
      indicator_id: "DEMO-I2",
      topic_id: "DEMO-T2",
      description: "列方程解决简单实际问题并检验",
      curriculum_ref: "DEMO-CR02",
      curriculum_page: "合成示例，无官方页码",
      textbook_pages: "合成示例，无官方页码",
    },
    {
      indicator_id: "DEMO-I3",
      topic_id: "DEMO-T3",
      description: "运用不等式性质求解，负数乘除时改变方向",
      curriculum_ref: "DEMO-CR03",
      curriculum_page: "合成示例，无官方页码",
      textbook_pages: "合成示例，无官方页码",
    },
    {
      indicator_id: "DEMO-I4",
      topic_id: "DEMO-T1",
      description: "识别同类项、去括号并化简",
      curriculum_ref: "DEMO-CR01",
      curriculum_page: "合成示例，无官方页码",
      textbook_pages: "合成示例，无官方页码",
    },
  ],
  links: [
    {
      from_id: "DEMO-I4",
      to_id: "DEMO-I1",
      relation: "prerequisite",
      basis: "合成关系示例：整式加减与去括号是移项前需要先具备的操作。",
    },
    {
      from_id: "DEMO-I1",
      to_id: "DEMO-I2",
      relation: "prerequisite",
      basis: "合成关系示例：先能解方程，才谈得上用方程建模。",
    },
  ],
};

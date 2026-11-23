import type { Material } from "../../types";

const syntheticLocator = {
  source_file: "项目自编合成样例",
  pdf_page: null,
  printed_page: null,
  verification_status: "unverified" as const,
};

/** 示例材料：题目、学习卡与教材条目各一份 */
export const demoMaterials: Record<string, Material> = {
  "DEMO-M1": {
    material_id: "DEMO-M1",
    material_type: "card",
    topic_ids: ["DEMO-T2"],
    indicator_ids: ["DEMO-I1"],
    content:
      "求解时，等式两边同时除以同一个非零数，等式仍成立。得到结果后，把它代入原方程，分别计算等号两边并比较。",
    source_locator: syntheticLocator,
    reviewed_variant_tag: "concept",
  },
  "DEMO-M2": {
    material_id: "DEMO-M2",
    material_type: "question",
    topic_ids: ["DEMO-T2"],
    indicator_ids: ["DEMO-I1"],
    content: "解方程：3x − 5 = x + 7。",
    source_locator: syntheticLocator,
    reference_answer: "x = 6",
    reference_explanation:
      "两边同时减去 x：2x − 5 = 7；两边同时加 5：2x = 12；两边同时除以 2：x = 6。检验：3×6 − 5 = 13，6 + 7 = 13。",
    max_score: 3,
    rubric_points: [
      { point_id: "DEMO-M2P1", description: "写出移项后的等式", score: 1 },
      { point_id: "DEMO-M2P2", description: "正确合并同类项并求出解", score: 1 },
      { point_id: "DEMO-M2P3", description: "代入检验并写出结论", score: 1 },
    ],
    reviewed_variant_tag: "standard",
  },
  "DEMO-M3": {
    material_id: "DEMO-M3",
    material_type: "question",
    topic_ids: ["DEMO-T2"],
    indicator_ids: ["DEMO-I1"],
    content: "解方程：2(x + 1) = x + 6。",
    source_locator: syntheticLocator,
    reference_answer: "x = 4",
    reference_explanation:
      "先去括号：2x + 2 = x + 6；两边同时减去 x：x + 2 = 6；两边同时减 2：x = 4。",
    max_score: 3,
    rubric_points: [
      { point_id: "DEMO-M3P1", description: "正确去括号", score: 1 },
      { point_id: "DEMO-M3P2", description: "移项并合并同类项", score: 1 },
      { point_id: "DEMO-M3P3", description: "写出检验过程", score: 1 },
    ],
    reviewed_variant_tag: "variation",
  },
  "DEMO-M4": {
    material_id: "DEMO-M4",
    material_type: "textbook",
    topic_ids: ["DEMO-T2"],
    indicator_ids: ["DEMO-I1"],
    content:
      "合成例题：解方程 5x − 2 = 3x + 6。两边同时减去 3x，得 2x − 2 = 6；两边同时加 2，得 2x = 8；两边同时除以 2，得 x = 4。",
    source_locator: syntheticLocator,
    reviewed_variant_tag: "concept",
  },
  "DEMO-M5": {
    material_id: "DEMO-M5",
    material_type: "question",
    topic_ids: ["DEMO-T2"],
    indicator_ids: ["DEMO-I1"],
    content: "解方程 4(x − 1) = 2x + 8，并检验。",
    source_locator: syntheticLocator,
    max_score: 3,
    reviewed_variant_tag: "variation",
  },
};

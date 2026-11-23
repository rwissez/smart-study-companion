import { describe, expect, it } from "vitest";
import { getMaterial, getRecommendations, getStudentResult, readRecord } from "../demoService";
import { demoClassResults } from "./classes";
import { demoRecords } from "./evidence";
import { demoStudentResults } from "./students";

describe("合成证据与建议", () => {
  it("能从部分得分追到原题、评分点和有依据的练习", async () => {
    const record = await readRecord("DEMO-RUN", "DEMO-R1");
    const question = await getMaterial(record.material_id, "teacher");
    expect(record.solution_steps?.at(-1)).toContain(record.final_answer);
    expect(record.rubric_scores?.reduce((total, point) => total + point.score, 0)).toBe(record.score);
    for (const point of record.rubric_scores ?? []) {
      expect(question.rubric_points?.some((item) => item.point_id === point.point_id)).toBe(true);
    }
    const bundle = await getRecommendations("DEMO-RUN", "DEMO-S1", "DEMO-N1");
    expect(bundle.recommendations[0].reason).toContain("移项正确");
    expect(bundle.recommendations[0].goal).toContain("检");
  });

  it("给表现较好的学生提供未做过的变式", async () => {
    const bundle = await getRecommendations("DEMO-RUN", "DEMO-S2", "DEMO-N1");
    const activity = bundle.recommendations[0].activities[0];
    expect(activity.previously_attempted).toBe(false);
    expect(Object.values(demoRecords).some((record) =>
      record.student_id === "DEMO-S2" && record.material_id === activity.material_id)).toBe(false);
  });

  it("区分班级有效零分与学生无可用结果", () => {
    expect(demoClassResults["DEMO-B"]["DEMO-N1"].metrics[0].value).toBe(0);
    expect(demoStudentResults["DEMO-S3"]["DEMO-N1"].metrics.every((metric) => metric.value === null)).toBe(true);
  });

  it("按示例身份返回材料，学生材料去掉教师字段", async () => {
    expect(await getMaterial("DEMO-M2", "teacher")).toHaveProperty("reference_answer");
    const material = await getMaterial("DEMO-M2", "student");
    expect(material).not.toHaveProperty("reference_answer");
    expect(material).not.toHaveProperty("reference_explanation");
    expect(material).not.toHaveProperty("rubric_points");
    expect(material.source_locator.source_file).toBe("项目自编合成样例");
  });

  it("将补充记录及建议绑定到新快照，原快照仍可查看", async () => {
    const [before, after, recommendation] = await Promise.all([
      getStudentResult("DEMO-RUN", "DEMO-S3", "DEMO-N1"),
      getStudentResult("DEMO-RUN-OBSERVED", "DEMO-S3", "DEMO-N1"),
      getRecommendations("DEMO-RUN-OBSERVED", "DEMO-S3", "DEMO-N1"),
    ]);
    expect(before.metrics.every((metric) => metric.value === null)).toBe(true);
    expect(after.dataset_id).not.toBe(before.dataset_id);
    expect(after.metrics[0].evidence_refs[0].run_id).toBe(after.run_id);
    expect(recommendation.recommendations[0].goal).toBe("补写代入检验");
    await expect(readRecord(before.run_id, "DEMO-R-OBSERVED")).rejects.toMatchObject({ status: 404 });
  });
});

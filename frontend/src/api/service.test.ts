import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createExport, normalizeCaseStudentIds } from "./service";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "Content-Type": "application/json" }),
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const fetchMock = vi.fn<typeof fetch>(async () =>
  jsonResponse({
    data: { export_id: "exp-1", snapshot_id: "snap-1", job_id: "job-1", status: "queued" },
    request_id: "req-1",
    warnings: [],
  }),
);

function bodyOfCall(index = 0): Record<string, unknown> {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

describe("createExport 提交案例学生", () => {
  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("选中的案例学生去重排序后放进 case_student_ids", async () => {
    await createExport("run-1", { caseStudentIds: ["S02", "S01", "S02"] });

    expect(bodyOfCall()).toEqual({ format: "zip", case_student_ids: ["S01", "S02"] });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/runs/run-1/exports");
  });

  it("未选择案例学生时请求体不包含 case_student_ids", async () => {
    await createExport("run-1");
    expect(bodyOfCall()).toEqual({ format: "zip" });
    expect("case_student_ids" in bodyOfCall()).toBe(false);

    await createExport("run-1", { caseStudentIds: [] });
    expect(bodyOfCall(1)).toEqual({ format: "zip" });
    expect("case_student_ids" in bodyOfCall(1)).toBe(false);
  });

  it("只有空白编号时按未选择处理", async () => {
    await createExport("run-1", { caseStudentIds: ["  ", ""] });
    expect(bodyOfCall()).toEqual({ format: "zip" });
  });
});

describe("normalizeCaseStudentIds", () => {
  it("去重、去空白并排序", () => {
    expect(normalizeCaseStudentIds(["S02", " S01 ", "S02", ""])).toEqual(["S01", "S02"]);
    expect(normalizeCaseStudentIds(undefined)).toEqual([]);
    expect(normalizeCaseStudentIds([])).toEqual([]);
  });
});

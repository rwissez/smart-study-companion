import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  listRuns: vi.fn(),
  listJobs: vi.fn(),
  listStudents: vi.fn(),
  getExportPreview: vi.fn(),
  preflightExport: vi.fn(),
  createExport: vi.fn(),
}));

vi.mock("../../app/session", () => ({
  useSession: () => ({
    scopeKey: { mode: "api", epoch: 1, datasetId: "dataset-1" },
    session: { scope: { capabilities: ["export_run"] } },
    switchDataset: async () => null,
  }),
}));

vi.mock("../../components/jobs/JobProgress", () => ({
  JobProgress: () => null,
}));

vi.mock("../../api/service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/service")>();
  return {
    ...actual,
    listDatasets: () => mocks.listDatasets(),
    listRuns: () => mocks.listRuns(),
    listJobs: () => mocks.listJobs(),
    listStudents: () => mocks.listStudents(),
    getExportPreview: () => mocks.getExportPreview(),
    preflightExport: (...args: unknown[]) => mocks.preflightExport(...args),
    createExport: (...args: unknown[]) => mocks.createExport(...args),
  };
});

import { DataPage } from "./DataPage";

const DATASETS = [
  {
    dataset_id: "dataset-1",
    dataset_family_id: "family-1",
    status: "ready",
    state: "ready",
    created_at: "2026-11-27T00:00:00Z",
    synthetic: true,
    display_name: "案例学生测试数据集",
    student_count: 3,
    class_count: 1,
    catalog_version: "catalog-1",
    table_row_counts: {},
  },
];

const RUNS = [
  {
    run_id: "run-1",
    dataset_id: "dataset-1",
    catalog_version: "catalog-1",
    knowledge_version: "knowledge-1",
    rule_version: "rule-1",
    status: "succeeded",
    created_at: "2026-11-27T00:00:00Z",
    node_ids: ["N01"],
  },
];

const STUDENTS = ["S01", "S02", "S03"].map((studentId) => ({
  student_id: studentId,
  class_id: "C1",
  grade: "7",
  observed_event_count: 3,
  graded_event_count: 2,
}));

const READY_PREFLIGHT = {
  ready: true,
  blocking: [],
  warnings: [],
  coverage: { students: 3, classes: 1, cases: 2 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/?tab=exports"]}>
        <DataPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole("checkbox", { name: "S01（C1）" });
}

function exportButton() {
  return screen.getByRole("button", { name: "生成导出" });
}

async function selectCase(studentId: string) {
  await userEvent.click(screen.getByRole("checkbox", { name: `${studentId}（C1）` }));
}

describe("成果导出使用案例学生选择", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.listDatasets.mockResolvedValue(DATASETS);
    mocks.listRuns.mockResolvedValue(RUNS);
    mocks.listJobs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 30 });
    mocks.listStudents.mockResolvedValue({ items: STUDENTS, total: STUDENTS.length, page: 1, page_size: 200 });
    mocks.getExportPreview.mockResolvedValue({ items: [], total: 0 });
    mocks.preflightExport.mockResolvedValue(READY_PREFLIGHT);
    mocks.createExport.mockResolvedValue({
      export_id: "exp-1",
      snapshot_id: "snap-1",
      job_id: "job-1",
      status: "queued",
    });
  });

  it("选 2 人时预检与正式导出使用同一组学生", async () => {
    await renderPage();

    await selectCase("S02");
    await selectCase("S01");
    await waitFor(() => expect(exportButton()).toBeEnabled());

    expect(mocks.preflightExport.mock.calls.at(-1)).toEqual(["run-1", { caseStudentIds: ["S01", "S02"] }]);

    await userEvent.click(exportButton());
    await waitFor(() => expect(mocks.createExport).toHaveBeenCalledTimes(1));
    expect(mocks.createExport).toHaveBeenCalledWith("run-1", { caseStudentIds: ["S01", "S02"] });
  });

  it("未勾选案例学生时导出不指定学生，交给后端自动选择", async () => {
    await renderPage();
    await waitFor(() => expect(exportButton()).toBeEnabled());

    await userEvent.click(exportButton());
    await waitFor(() => expect(mocks.createExport).toHaveBeenCalledTimes(1));
    expect(mocks.createExport).toHaveBeenCalledWith("run-1", undefined);
  });

  it("导出过程中改选不影响本次请求已确定的学生集合", async () => {
    const pending = deferred<{
      export_id: string;
      snapshot_id: string;
      job_id: string;
      status: string;
    }>();
    mocks.createExport.mockReturnValue(pending.promise);
    await renderPage();

    await selectCase("S01");
    await selectCase("S02");
    await waitFor(() => expect(exportButton()).toBeEnabled());

    await userEvent.click(exportButton());
    await waitFor(() => expect(mocks.createExport).toHaveBeenCalledTimes(1));

    await selectCase("S03");
    expect(mocks.createExport).toHaveBeenCalledWith("run-1", { caseStudentIds: ["S01", "S02"] });

    pending.resolve({ export_id: "exp-1", snapshot_id: "snap-1", job_id: "job-1", status: "queued" });
    await waitFor(() => expect(exportButton()).toBeEnabled());
  });
});

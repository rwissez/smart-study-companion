import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { JobDetail } from "../../api/types";

const mocks = vi.hoisted(() => ({
  getJob: vi.fn(),
  getJobEvents: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
}));

vi.mock("../../api/service", () => ({
  getJob: (...args: unknown[]) => mocks.getJob(...args),
  getJobEvents: (...args: unknown[]) => mocks.getJobEvents(...args),
  cancelJob: (...args: unknown[]) => mocks.cancelJob(...args),
  retryJob: (...args: unknown[]) => mocks.retryJob(...args),
}));

import { JobProgress } from "./JobProgress";

const failedJob: JobDetail = {
  job_id: "job-1",
  kind: "student_update",
  status: "failed",
  stage: "analysis",
  scope: { dataset_id: "dataset-1", student_id: "D05", node_id: "N01" },
  created_at: "2026-11-27T12:00:00Z",
  updated_at: "2026-11-27T12:01:00Z",
  result_ref: null,
  error: { code: "MODEL_TIMEOUT", message: "模型请求超时" },
  warnings: [],
  steps: [],
  review_status: null,
  publication_status: null,
  plan_version: null,
  retry: {
    root_job_id: "job-1",
    retry_of_job_id: null,
    retry_job_id: null,
    manual_retry_count: 0,
    can_retry: false,
    retry_block_reason: null,
  },
  result: null,
  cancel_requested: false,
};

describe("JobProgress 来源与失败重试", () => {
  it("显示任务来源：哪个学生、哪个节点、由什么操作触发，并给出重新提交入口", async () => {
    mocks.getJob.mockResolvedValue(failedJob);
    mocks.getJobEvents.mockResolvedValue({ items: [], next_sequence: 0, has_more: false });
    const onRetrySubmit = vi.fn();

    render(<JobProgress jobId="job-1" origin="重新分析" onRetrySubmit={onRetrySubmit} />);

    expect(await screen.findByText("来源：重新分析 · 学生 D05 · 节点 N01")).toBeInTheDocument();
    expect(screen.getByText("模型请求超时")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新提交" }));
    expect(onRetrySubmit).toHaveBeenCalledTimes(1);
  });
});

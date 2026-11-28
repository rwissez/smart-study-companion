import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanView } from "../../api/types";

const mocks = vi.hoisted(() => ({
  getPlan: vi.fn(),
  listLearningEvents: vi.fn(),
  listRevisions: vi.fn(),
  previewRevision: vi.fn(),
  publishRevision: vi.fn(),
  searchMaterials: vi.fn(),
}));

vi.mock("../../app/session", () => ({
  useSession: () => ({
    session: { scope: { role: "teacher", dataset_id: "dataset-1" } },
    scopeKey: { mode: "api", epoch: 1, datasetId: "dataset-1" },
  }),
}));

vi.mock("../../api/service", () => ({
  dataSource: "api",
  isDemo: false,
  createSession: vi.fn(),
  getPlan: (...args: unknown[]) => mocks.getPlan(...args),
  listLearningEvents: (...args: unknown[]) => mocks.listLearningEvents(...args),
  listRevisions: (...args: unknown[]) => mocks.listRevisions(...args),
  previewRevision: (...args: unknown[]) => mocks.previewRevision(...args),
  publishRevision: (...args: unknown[]) => mocks.publishRevision(...args),
  searchMaterials: (...args: unknown[]) => mocks.searchMaterials(...args),
}));

import { PlanEditor } from "./PlanEditor";

function planView(version: string): PlanView {
  return {
    plan_version: version,
    freshness: "current",
    limitations: [],
    analysis_version: "analysis-1",
    applicability: null,
    plan: {
      run_id: "run-1",
      dataset_id: "dataset-1",
      catalog_version: "catalog-1",
      student_id: "D05",
      node_id: "N01",
      recommendations: [
        {
          recommendation_id: "rec-1",
          indicator_id: "M1",
          finding_ids: [],
          goal: `目标 ${version}`,
          reason: "原方案理由",
          student_intro: "先看定义。",
          activities: [
            {
              order: 1,
              material_id: "MAT-01",
              purpose: "练习",
              hint_levels: ["提示一", "提示二", "提示三"],
              observation_points: [],
              previously_attempted: false,
              activity_id: "act-1",
              group_id: "group-1",
              knowledge_ids: [],
              activity_kind: "question",
              state: "not_started",
              title: "去括号练习",
              expected_outcome: "",
              estimate_minutes: 5,
              dependencies: [],
              evidence_refs: [],
              exposure_mode: "practice",
              repeat_reason: null,
              content_ref: null,
              content_version: null,
              source_label: "合成材料",
            },
          ],
          adjustment_conditions: [],
          review_status: "validated",
          generation_method: "rule",
          strategy_types: [],
          kp_ids: [],
          evidence_refs: [],
          review_info: null,
          group_id: "group-1",
        },
      ],
      validation_issues: [],
      plan_version: version,
      analysis_version: "analysis-1",
      knowledge_version: null,
      parent_plan_version: null,
      revision: 1,
      publication_status: "published",
      created_at: "2026-11-27T12:00:00Z",
      task_groups: [],
      dataset_family_id: "family-1",
      limitations: [],
    },
  };
}

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <button type="button" onClick={() => setOpen(true)}>重新打开编辑器</button>
        <PlanEditor open={open} studentId="D05" nodeId="N01" onClose={() => setOpen(false)} onPublished={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("PlanEditor 发布流程", () => {
  let version = "planv-1";
  let previewNo = 0;

  beforeEach(() => {
    vi.clearAllMocks();
    version = "planv-1";
    previewNo = 0;
    mocks.getPlan.mockImplementation(async () => planView(version));
    mocks.listLearningEvents.mockResolvedValue({ items: [] });
    mocks.listRevisions.mockResolvedValue({ items: [] });
    mocks.previewRevision.mockImplementation(async () => {
      previewNo += 1;
      return {
        preview_id: `preview-${previewNo}`,
        draft_hash: `draft-${previewNo}`,
        warning_digest: `digest-${previewNo}`,
        warnings: [],
        expires_at: "2026-11-27T13:00:00Z",
      };
    });
    mocks.publishRevision.mockImplementation(async () => {
      version = "planv-2";
      return {
        revision_id: "rev-2",
        plan_version: "planv-2",
        review_status: "human_confirmed",
        publication_status: "published",
      };
    });
  });

  it("修改理由可以连续输入；检查与发布是两步；发布后可基于新版本继续编辑", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const reason = await screen.findByPlaceholderText("例如：学生反馈开场白太长，压缩后重新安排");
    await user.type(reason, "压缩开场白，减少负担");
    // 修复前每次按键焦点都会跳走，只剩最后一个字符。
    expect(reason).toHaveValue("压缩开场白，减少负担");

    await user.click(screen.getByRole("button", { name: "检查草稿（第一步）" }));
    expect(await screen.findByText("检查通过，等待确认")).toBeInTheDocument();
    expect(mocks.previewRevision).toHaveBeenCalledTimes(1);
    expect(mocks.previewRevision.mock.calls[0][2]).toMatchObject({ basePlanVersion: "planv-1" });

    await user.click(screen.getByRole("button", { name: "确认发布（第二步）" }));
    expect(await screen.findByText("方案已发布")).toBeInTheDocument();
    expect(mocks.publishRevision).toHaveBeenCalledTimes(1);

    // 发布后不重开弹窗也应能基于新正式版本继续编辑。
    await user.click(screen.getByRole("button", { name: "继续编辑新版本" }));
    expect(await screen.findByRole("button", { name: "检查草稿（第一步）" })).toBeInTheDocument();
    expect(screen.queryByText("方案已发布")).toBeNull();

    const secondReason = screen.getByPlaceholderText("例如：学生反馈开场白太长，压缩后重新安排");
    await user.type(secondReason, "再压缩一题");
    await user.click(screen.getByRole("button", { name: "检查草稿（第一步）" }));
    await waitFor(() => expect(mocks.previewRevision).toHaveBeenCalledTimes(2));
    expect(mocks.previewRevision.mock.calls[1][2]).toMatchObject({ basePlanVersion: "planv-2" });

    await user.click(screen.getByRole("button", { name: "确认发布（第二步）" }));
    await waitFor(() => expect(mocks.publishRevision).toHaveBeenCalledTimes(2));

    // 关闭再打开：不能把上一次的“方案已发布”面板带回来。
    await user.click(screen.getByRole("button", { name: "关闭并刷新" }));
    await user.click(screen.getByRole("button", { name: "重新打开编辑器" }));
    expect(await screen.findByRole("button", { name: "检查草稿（第一步）" })).toBeInTheDocument();
    expect(screen.queryByText("方案已发布")).toBeNull();
  });
});

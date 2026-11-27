import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanOptimizeResult, PlanView } from "../../api/types";

const mocks = vi.hoisted(() => ({
  getPlan: vi.fn(),
  listLearningEvents: vi.fn(),
  listRevisions: vi.fn(),
  optimizePlanRevision: vi.fn(),
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
  optimizePlanRevision: (...args: unknown[]) => mocks.optimizePlanRevision(...args),
  previewRevision: (...args: unknown[]) => mocks.previewRevision(...args),
  publishRevision: (...args: unknown[]) => mocks.publishRevision(...args),
  searchMaterials: (...args: unknown[]) => mocks.searchMaterials(...args),
}));

import { PlanEditor } from "./PlanEditor";

function planActivity(order: number, activityId: string) {
  return {
    order,
    material_id: `MAT-0${order}`,
    purpose: "练习",
    hint_levels: ["提示一", "提示二", "提示三"],
    observation_points: [],
    previously_attempted: false,
    activity_id: activityId,
    group_id: "group-1",
    knowledge_ids: [],
    activity_kind: "question" as const,
    state: "not_started" as const,
    title: `去括号练习 ${order}`,
    expected_outcome: "",
    estimate_minutes: 5,
    dependencies: [],
    evidence_refs: [],
    exposure_mode: "practice" as const,
    repeat_reason: null,
    content_ref: null,
    content_version: null,
    source_label: "合成材料",
  };
}

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
          goal: "围绕去括号安排本轮学习",
          reason: "原方案理由",
          student_intro: "先看定义。",
          activities: [
            planActivity(1, "act-1"),
            planActivity(2, "act-2"),
            planActivity(3, "act-3"),
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

function optimizeResult(overrides: Partial<PlanOptimizeResult>): PlanOptimizeResult {
  return {
    status: "proposal",
    read_only: true,
    model_used: true,
    reason: "去掉重复练习，保留独立检查",
    limitations: [],
    issues: [],
    base_plan_version: "planv-1",
    candidate_count: 4,
    pool_note: "候选池只取本节点登记的可用题目。",
    changes: [],
    blocked_reasons: [],
    revision_changes: null,
    preview_note:
      "这些只是只读建议，不会改动正式方案；确认后仍要先检查草稿（preview），通过后再确认发布。",
    ...overrides,
  };
}

const PREVIEW = {
  preview_id: "preview-1",
  draft_hash: "draft-1",
  warning_digest: "digest-1",
  warnings: [],
  expires_at: "2026-11-27T13:00:00Z",
};

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

describe("PlanEditor 按意图优化", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getPlan.mockImplementation(async () => planView("planv-1"));
    mocks.listLearningEvents.mockResolvedValue({ items: [] });
    mocks.listRevisions.mockResolvedValue({ items: [] });
    mocks.previewRevision.mockResolvedValue(PREVIEW);
    mocks.publishRevision.mockResolvedValue({
      revision_id: "rev-2",
      plan_version: "planv-2",
      review_status: "human_confirmed",
      publication_status: "published",
    });
  });

  it("输入意图生成只读建议；确认带入草稿后才走 preview → publish", async () => {
    const intent = "减轻书写负担，去掉一道重复练习";
    mocks.optimizePlanRevision.mockResolvedValue(
      optimizeResult({
        changes: [
          {
            op: "remove",
            group_key: "G1",
            group_goal: "围绕去括号安排本轮学习",
            activity_key: "A2",
            activity_title: "去括号练习 2",
            activity_source: "current_plan",
            from_activity_key: null,
            from_activity_title: null,
            position: null,
            reason: "与第一题结构重复，删掉后负担更合理",
            applicable: true,
            apply_blocker: null,
          },
        ],
        revision_changes: {
          activities: [planActivity(1, "act-1"), planActivity(2, "act-3")],
        },
      }),
    );
    const user = userEvent.setup();
    render(<Harness />);

    const input = await screen.findByPlaceholderText("例如：减轻书写负担，把重复练习换成一道独立检查题");
    await user.type(input, intent);
    await user.click(screen.getByRole("button", { name: "生成优化建议" }));

    expect(await screen.findByText("已生成候选建议（只读，不发布）")).toBeInTheDocument();
    expect(mocks.optimizePlanRevision).toHaveBeenCalledWith("run-1", "D05", {
      nodeId: "N01",
      basePlanVersion: "planv-1",
      intent,
    });
    expect(screen.getByText(/去掉重复练习，保留独立检查/)).toBeInTheDocument();
    expect(screen.getByText(/来源：当前方案/)).toBeInTheDocument();
    expect(screen.getByText(/不会改动正式方案/)).toBeInTheDocument();
    // 只是建议：没有预览，也没有发布。
    expect(mocks.previewRevision).not.toHaveBeenCalled();
    expect(mocks.publishRevision).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "把可套用的调整带入草稿（仍需检查）" }));
    await user.click(screen.getByRole("button", { name: "检查草稿（第一步）" }));

    await waitFor(() => expect(mocks.previewRevision).toHaveBeenCalledTimes(1));
    const payload = mocks.previewRevision.mock.calls[0][2] as {
      basePlanVersion: string;
      changes: { activities?: Array<{ activity_id: string; order: number }> };
    };
    expect(payload.basePlanVersion).toBe("planv-1");
    expect(payload.changes.activities?.map((item) => item.activity_id)).toEqual(["act-1", "act-3"]);
    expect(payload.changes.activities?.map((item) => item.order)).toEqual([1, 2]);

    await user.click(await screen.findByRole("button", { name: "确认发布（第二步）" }));
    await waitFor(() => expect(mocks.publishRevision).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("方案已发布")).toBeInTheDocument();
  });

  it("模型不可用时明确降级，不提供带入草稿的入口", async () => {
    mocks.optimizePlanRevision.mockResolvedValue(
      optimizeResult({
        status: "unavailable",
        model_used: false,
        reason: "",
        limitations: ["教师意图优化未完成（MODEL_NOT_CONFIGURED），保留当前方案"],
        changes: [],
      }),
    );
    const user = userEvent.setup();
    render(<Harness />);

    const input = await screen.findByPlaceholderText("例如：减轻书写负担，把重复练习换成一道独立检查题");
    await user.type(input, "减轻负担");
    await user.click(screen.getByRole("button", { name: "生成优化建议" }));

    expect(await screen.findByText("模型不可用，保留当前方案")).toBeInTheDocument();
    expect(screen.getByText(/MODEL_NOT_CONFIGURED/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "把可套用的调整带入草稿（仍需检查）" })).toBeNull();
    expect(screen.queryByText("检查通过，等待确认")).toBeNull();
    expect(mocks.previewRevision).not.toHaveBeenCalled();
    expect(mocks.publishRevision).not.toHaveBeenCalled();
  });

  it("新增活动建议标注来源与限制，只读展示", async () => {
    mocks.optimizePlanRevision.mockResolvedValue(
      optimizeResult({
        changes: [
          {
            op: "add",
            group_key: "G1",
            group_goal: "围绕去括号安排本轮学习",
            activity_key: "MAT-09",
            activity_title: "化简含括号的式子（MAT-09）",
            activity_source: "catalog",
            from_activity_key: null,
            from_activity_title: null,
            position: null,
            reason: "用同目标的独立检查替代重复练习",
            applicable: false,
            apply_blocker: "新增或替换活动需要材料和三级提示，优化只给活动编号；请在活动列表里手动添加并填写三级提示，再检查草稿。",
          },
        ],
        blocked_reasons: [
          "新增或替换活动需要材料和三级提示，优化只给活动编号；请在活动列表里手动添加并填写三级提示，再检查草稿。",
        ],
      }),
    );
    const user = userEvent.setup();
    render(<Harness />);

    const input = await screen.findByPlaceholderText("例如：减轻书写负担，把重复练习换成一道独立检查题");
    await user.type(input, "加入独立检查题");
    await user.click(screen.getByRole("button", { name: "生成优化建议" }));

    expect(await screen.findByText(/化简含括号的式子（MAT-09）/)).toBeInTheDocument();
    expect(screen.getAllByText(/来源：正式目录候选/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/三级提示/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "把可套用的调整带入草稿（仍需检查）" })).toBeNull();
    expect(mocks.previewRevision).not.toHaveBeenCalled();
  });
});

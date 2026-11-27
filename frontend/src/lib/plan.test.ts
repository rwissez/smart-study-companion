import { describe, expect, it } from "vitest";
import { activityStatesFromEvents, groupProgress, mergeActivityState, nextActivity } from "./plan";
import type { LearningEventView, PlanActivity, TaskGroup } from "../api/types";

let sequence = 0;

function event(activityId: string, eventType: string, extra: Partial<LearningEventView> = {}): LearningEventView {
  sequence += 1;
  return {
    event_id: `e-${sequence}`,
    event_type: eventType,
    source: "system",
    occurred_at: `2026-11-20T08:00:0${sequence % 10}+00:00`,
    sequence,
    student_id: "S01",
    node_id: "N01",
    plan_version: "plan-1",
    group_id: "g1",
    activity_id: activityId,
    material_id: null,
    attempt_id: null,
    summary: eventType,
    payload: {},
    ...extra,
  };
}

function activity(id: string, state: PlanActivity["state"], dependencies: string[] = []): PlanActivity {
  return {
    order: 1,
    material_id: null,
    purpose: "",
    hint_levels: [],
    observation_points: [],
    previously_attempted: false,
    activity_id: id,
    group_id: "g1",
    knowledge_ids: [],
    activity_kind: "question",
    state,
    title: "",
    expected_outcome: "",
    estimate_minutes: 4,
    dependencies,
    evidence_refs: [],
    exposure_mode: null,
    repeat_reason: null,
    content_ref: null,
    content_version: null,
    source_label: null,
  };
}

function group(ids: string[]): TaskGroup {
  return {
    group_id: "g1",
    goal: "目标",
    strategy_types: [],
    activity_ids: ids,
    order: 1,
    primary_strategy: null,
    auxiliary_strategies: [],
    goal_target_key: null,
    expected_outcome: "",
    estimated_minutes: 8,
    required_activity_ids: ids,
    evidence_refs: [],
    dependencies: [],
    student_intro: "",
    teacher_reason: "",
  };
}

describe("活动状态推导", () => {
  it("只认服务端事件，且完成后不被后续事件改回进行中", () => {
    const events = [
      event("a1", "activity_started"),
      event("a1", "answer_submitted"),
      event("a1", "activity_completed"),
      event("a1", "hint_requested"),
      event("a2", "hint_requested"),
    ];
    const states = activityStatesFromEvents(events, "plan-1");
    expect(states.a1).toBe("completed");
    expect(states.a2).toBe("in_progress");
  });

  it("忽略其他方案版本的事件", () => {
    const states = activityStatesFromEvents([event("a1", "activity_completed", { plan_version: "plan-2" })], "plan-1");
    expect(states.a1).toBeUndefined();
  });

  it("快照状态在缺少事件时兜底，事件优先", () => {
    expect(mergeActivityState(activity("a1", "not_started"), { a1: "in_progress" })).toBe("in_progress");
    expect(mergeActivityState(activity("a1", "not_started"), {})).toBe("not_started");
  });
});

describe("任务组与下一步", () => {
  it("必需活动完成计数以服务端状态为准，取消不算完成", () => {
    const items = [activity("a1", "not_started"), activity("a2", "not_started")];
    const progress = groupProgress(group(["a1", "a2"]), items, { a1: "completed", a2: "cancelled" });
    expect(progress).toEqual({ groupId: "g1", requiredTotal: 2, requiredCompleted: 1 });
  });

  it("依赖未满足的活动不是当前可执行项", () => {
    const items = [activity("a1", "completed"), activity("a2", "not_started", ["a1"]), activity("a3", "not_started", ["a2"])];
    expect(nextActivity(items, { a1: "completed" })?.activity_id).toBe("a2");
    expect(nextActivity([activity("a3", "not_started", ["missing"])], {})).toBeNull();
  });
});

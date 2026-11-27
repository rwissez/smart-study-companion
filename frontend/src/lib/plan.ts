import type { ActivityState, LearningEventView, PlanActivity, TaskGroup } from "../api/types";

/**
 * 方案响应里的 activities[].state 是发布时的快照，之后的活动推进只写学习事件。
 * 这里只按服务端真实事件推导显示状态，不自己判断“是否完成”。
 */
export function activityStatesFromEvents(
  events: LearningEventView[],
  planVersion: string,
): Record<string, ActivityState> {
  const ordered = [...events]
    .filter((event) => event.plan_version === planVersion && event.activity_id)
    .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0) || a.occurred_at.localeCompare(b.occurred_at));

  const states: Record<string, ActivityState> = {};
  for (const event of ordered) {
    const activityId = event.activity_id as string;
    if (states[activityId] === "completed" || states[activityId] === "cancelled") continue;
    if (
      event.event_type === "activity_completed" ||
      event.event_type === "study_material_completed" ||
      event.event_type === "micro_lesson_completed"
    ) {
      states[activityId] = "completed";
    } else if (
      event.event_type === "activity_started" ||
      event.event_type === "answer_submitted" ||
      event.event_type === "hint_requested" ||
      event.event_type === "micro_lesson_opened" ||
      event.event_type === "agent_help_used"
    ) {
      states[activityId] = "in_progress";
    }
  }
  return states;
}

export function mergeActivityState(
  activity: PlanActivity,
  derived: Record<string, ActivityState> | undefined,
): ActivityState {
  return derived?.[activity.activity_id] ?? activity.state;
}

export interface GroupProgress {
  groupId: string;
  requiredTotal: number;
  requiredCompleted: number;
}

export function groupProgress(
  group: TaskGroup,
  activities: PlanActivity[],
  derived: Record<string, ActivityState> | undefined,
): GroupProgress {
  const required = group.required_activity_ids.length
    ? group.required_activity_ids
    : activities.filter((activity) => activity.group_id === group.group_id).map((activity) => activity.activity_id);
  const completed = required.filter(
    (activityId) => {
      const activity = activities.find((item) => item.activity_id === activityId);
      return activity ? mergeActivityState(activity, derived) === "completed" : false;
    },
  ).length;
  return { groupId: group.group_id, requiredTotal: required.length, requiredCompleted: completed };
}

/** 当前可执行活动：第一个未完成且依赖已满足的活动；不自动替学生选择作答。 */
export function nextActivity(
  activities: PlanActivity[],
  derived: Record<string, ActivityState> | undefined,
): PlanActivity | null {
  const completed = new Set(
    activities.filter((activity) => mergeActivityState(activity, derived) === "completed").map((activity) => activity.activity_id),
  );
  return (
    activities.find((activity) => {
      const state = mergeActivityState(activity, derived);
      if (state === "completed" || state === "cancelled") return false;
      return activity.dependencies.every((dependency) => completed.has(dependency));
    }) ?? null
  );
}

export function totalMinutes(groups: TaskGroup[]): number {
  return groups.reduce((total, group) => total + (group.estimated_minutes || 0), 0);
}

import type { ActivityState, PlanActivity, TaskGroup } from "../../api/types";
import { activityKindLabel, activityStateLabel } from "../../lib/format";
import { groupProgress, mergeActivityState } from "../../lib/plan";
import { IconArrowRight, IconCheck, IconLock } from "../icons";

interface Props {
  groups: TaskGroup[];
  activities: PlanActivity[];
  names: Record<string, string>;
  derivedStates?: Record<string, ActivityState>;
  mode: "teacher" | "student";
  currentActivityId?: string | null;
  onOpen?: (activity: PlanActivity) => void;
  canOpen?: (activity: PlanActivity) => boolean;
}

/**
 * 任务组与活动列表。状态以服务端事件推导值为准，group 完成度只做展示计数，
 * 不在这里触发组完成，也不把 cancelled 算成完成。
 */
export function TaskGroupView({
  groups,
  activities,
  names,
  derivedStates,
  mode,
  currentActivityId,
  onOpen,
  canOpen,
}: Props) {
  if (groups.length === 0) {
    return <p className="muted small">这个方案版本没有任务组。</p>;
  }
  const ordered = [...groups].sort((a, b) => a.order - b.order);
  return (
    <div className="groupList">
      {ordered.map((group, groupIndex) => {
        const progress = groupProgress(group, activities, derivedStates);
        const groupActivities = [...activities]
          .filter((activity) => group.activity_ids.includes(activity.activity_id) || activity.group_id === group.group_id)
          .sort((a, b) => a.order - b.order);
        return (
          <section className="group" key={group.group_id} aria-labelledby={`group-${group.group_id}`}>
            <div className="groupHead">
              <div>
                <div className="groupIndex">任务组 {groupIndex + 1} · {group.estimated_minutes || 0} 分钟</div>
                <h3 className="groupGoal" id={`group-${group.group_id}`}>{group.goal}</h3>
                {mode === "student" && group.student_intro ? <p className="groupIntro">{group.student_intro}</p> : null}
                {mode === "teacher" && group.teacher_reason ? <p className="groupIntro">教师依据：{group.teacher_reason}</p> : null}
              </div>
              <div className="row">
                <span className="small muted">必需活动</span>
                <span className="small tabular">{progress.requiredCompleted} / {progress.requiredTotal}</span>
                <div className="progressTrack" style={{ width: 120 }} aria-hidden="true">
                  <div
                    className="progressFill"
                    style={{ width: progress.requiredTotal ? `${(progress.requiredCompleted / progress.requiredTotal) * 100}%` : "0%" }}
                  />
                </div>
              </div>
            </div>
            <div className="activityList">
              {groupActivities.map((activity) => {
                const state = mergeActivityState(activity, derivedStates);
                const isCurrent = activity.activity_id === currentActivityId;
                const open = canOpen ? canOpen(activity) : state !== "cancelled";
                return (
                  <div className="activityRow" key={activity.activity_id}>
                    <div className="activityIndex">{groupIndex + 1}.{activity.order}</div>
                    <div className="activityMain">
                      <div className="activityTitle">
                        {activity.title || activity.purpose || `${activityKindLabel(activity.activity_kind)}活动`}
                      </div>
                      <div className="activityMeta">
                        <span className="tag">{activityKindLabel(activity.activity_kind)}</span>
                        <span>{activityStateLabel(state)}</span>
                        {group.goal_target_key ? <span>{names[group.goal_target_key] ?? "观察指标"}</span> : null}
                        <span>{activity.estimate_minutes} 分钟</span>
                        {activity.hint_levels.length ? <span>{activity.hint_levels.length} 级提示</span> : null}
                        {activity.previously_attempted ? <span>此前练过</span> : null}
                        {activity.dependencies.length ? <span>依赖 {activity.dependencies.length} 个活动</span> : null}
                      </div>
                      {activity.expected_outcome ? <p className="xs muted" style={{ marginTop: 4 }}>预期：{activity.expected_outcome}</p> : null}
                    </div>
                    <div className="activityActions">
                      {state === "completed" ? <span className="badge badgeSuccess"><IconCheck size={16} />已完成</span> : null}
                      {state === "cancelled" ? <span className="activityLocked">已取消，不计完成</span> : null}
                      {mode === "student" && onOpen ? (
                        state === "completed" ? (
                          <button type="button" className="btn btnSm" onClick={() => onOpen(activity)}>查看</button>
                        ) : open ? (
                          <button
                            type="button"
                            className={isCurrent ? "btn btnSm btnPrimary" : "btn btnSm"}
                            onClick={() => onOpen(activity)}
                          >
                            {state === "in_progress" ? "继续" : "开始"}
                            <IconArrowRight size={16} />
                          </button>
                        ) : (
                          <span className="activityLocked"><IconLock size={14} /> 依赖未满足</span>
                        )
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

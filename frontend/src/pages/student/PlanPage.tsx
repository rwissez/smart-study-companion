import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { getCatalog, getPlan, listLearningEvents } from "../../api/service";
import type { LearningEventView, PlanActivity, PlanView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { StatePanel } from "../../components/feedback/StatePanel";
import { TaskGroupView } from "../../components/learning/TaskGroupView";
import { activityStatesFromEvents, groupProgress, nextActivity } from "../../lib/plan";
import { formatDate } from "../../lib/format";
import { IconArrowRight } from "../../components/icons";

/** 学习计划：目标、进度、垂直任务组与活动列表。不提供学生手动生成或修改入口。 */
export function StudentPlanPage() {
  const { scopeKey, session } = useSession();
  const datasetId = scopeKey.datasetId;
  const studentId = session?.scope.student_id ?? null;
  const navigate = useNavigate();

  const plan = useQuery<PlanView>({
    queryKey: queryKeys.plan(scopeKey, studentId ?? "", null, null),
    queryFn: () => getPlan(datasetId as string, studentId as string, {}),
    enabled: Boolean(datasetId && studentId),
  });

  const events = useQuery<{ items: LearningEventView[] }>({
    queryKey: queryKeys.learningEvents(scopeKey, studentId ?? ""),
    queryFn: () => listLearningEvents(datasetId as string, studentId as string, { pageSize: 200 }),
    enabled: Boolean(datasetId && studentId),
  });

  const catalog = useQuery({
    queryKey: queryKeys.catalog(scopeKey, plan.data?.plan.catalog_version ?? null),
    queryFn: () => getCatalog(plan.data?.plan.catalog_version ?? null),
    enabled: Boolean(plan.data),
  });

  const names = useMemo(() => {
    const map: Record<string, string> = {};
    catalog.data?.indicators.forEach((indicator) => { map[indicator.indicator_id] = indicator.description; });
    return map;
  }, [catalog.data]);

  const planData = plan.data;
  const activities = planData?.plan.recommendations.flatMap((item) => item.activities) ?? [];
  // 方案里的 activities[].state 是发布时快照，推进状态只认服务端学习事件。
  const derived = useMemo(
    () => (planData ? activityStatesFromEvents(events.data?.items ?? [], planData.plan_version) : {}),
    [events.data, planData],
  );

  const current = useMemo(() => nextActivity(activities, derived), [activities, derived]);
  const groups = planData?.plan.task_groups ?? [];
  const totalRequired = groups.reduce(
    (total, group) => total + groupProgress(group, activities, derived).requiredTotal,
    0,
  );
  const doneRequired = groups.reduce(
    (total, group) => total + groupProgress(group, activities, derived).requiredCompleted,
    0,
  );

  const openActivity = (activity: PlanActivity) => {
    if (!planData) return;
    navigate(`/student/plan/${encodeURIComponent(planData.plan_version)}/activities/${encodeURIComponent(activity.activity_id)}`);
  };

  const completedIds = new Set(
    activities.filter((activity) => derived[activity.activity_id] === "completed").map((activity) => activity.activity_id),
  );
  const canOpen = (activity: PlanActivity) =>
    derived[activity.activity_id] === "completed" ||
    activity.dependencies.every((dependency) => completedIds.has(dependency));

  if (!datasetId || !studentId) {
    return <StatePanel kind="empty" title="学生会话缺少数据集或学生身份" message="请回到身份入口重新选择。" />;
  }

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">学习计划</h1>
          <p className="pageSub">
            {planData
              ? `发布于 ${formatDate(planData.plan.created_at)} · ${planData.plan.task_groups.length} 个任务组`
              : "读取当前正式方案"}
          </p>
        </div>
        {current ? (
          <div className="pageActions">
            <button type="button" className="btn btnPrimary" onClick={() => openActivity(current)}>
              {current.activity_id && derived[current.activity_id] === "in_progress" ? "继续学习" : "开始学习"}
              <IconArrowRight size={16} />
            </button>
          </div>
        ) : null}
      </div>

      {plan.isPending ? <StatePanel kind="loading" title="正在读取学习计划" /> : null}
      {plan.isError ? (
        <StatePanel
          kind={errorCode(plan.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
          title={errorCode(plan.error) === "RESULT_NOT_READY" ? "还没有正式学习计划" : "学习计划读取失败"}
          message={
            errorCode(plan.error) === "RESULT_NOT_READY"
              ? `${toErrorMessage(plan.error)} 方案生成、审核与发布由服务端完成；学生端不提供手动生成或修改入口。`
              : toErrorMessage(plan.error)
          }
          requestId={errorRequestId(plan.error)}
          onRetry={() => void plan.refetch()}
        />
      ) : null}

      {planData ? (
        <>
          <section className="section" aria-label="目标与进度">
            <div className="planHead">
              <div>
                <h2 className="planGoal">{planData.plan.recommendations[0]?.goal ?? "方案目标未提供"}</h2>
                <div className="planMeta" style={{ marginTop: 8 }}>
                  <span>必需活动 {doneRequired} / {totalRequired}</span>
                  <span>任务组 {groups.length} 个</span>
                  <span>{planData.plan.recommendations.length} 条建议</span>
                  <span>{planData.freshness === "current" ? "当前安排" : "历史安排"}</span>
                  <span>{planData.plan.publication_status === "published" ? "已发布" : "发布状态待确认"}</span>
                </div>
              </div>
              {current ? <span className="tag tagAccent">当前：{current.title || current.purpose || "下一步活动"}</span> : <span className="tag">全部必需活动已完成</span>}
            </div>
            <div className="progressTrack" style={{ marginTop: 12 }} aria-hidden="true">
              <div className="progressFill" style={{ width: totalRequired ? `${(doneRequired / totalRequired) * 100}%` : "0%" }} />
            </div>
            <p className="xs muted" style={{ marginTop: 6 }}>
              进度只统计服务端确认完成的必需活动；取消的活动不计入完成。
            </p>
            {planData.plan.recommendations[0]?.student_intro ? (
              <p className="prose reading" style={{ marginTop: 12 }}>{planData.plan.recommendations[0].student_intro}</p>
            ) : null}
            {planData.applicability ? (
              <div className="statePanel" style={{ marginTop: 12 }}>
                <span className="stateTitle">适用性复查：{planData.applicability.result === "applicable" ? "安排仍然适用" : "需要重新安排"}</span>
                <span className="small muted">复查时间 {formatDate(planData.applicability.checked_at)}</span>
                {planData.applicability.limitations.length ? (
                  <span className="small">限制：{planData.applicability.limitations.join("；")}</span>
                ) : null}
              </div>
            ) : null}
            {planData.limitations.length ? <p className="small muted">限制：{planData.limitations.join("；")}</p> : null}
          </section>

          <section className="section" aria-label="任务组">
            <div className="sectionHead">
              <h2 className="sectionTitle">任务组</h2>
              <span className="sectionHint">按 C 的任务组顺序排列，不按题型重排</span>
            </div>
            {events.isError ? (
              <p className="small muted">学习事件读取失败，活动状态暂以方案快照显示：{toErrorMessage(events.error)}</p>
            ) : null}
            <TaskGroupView
              groups={groups}
              activities={activities}
              names={names}
              derivedStates={derived}
              mode="student"
              currentActivityId={current?.activity_id ?? null}
              onOpen={openActivity}
              canOpen={canOpen}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}

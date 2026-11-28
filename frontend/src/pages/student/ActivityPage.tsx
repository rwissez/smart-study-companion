import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { getPlan } from "../../api/service";
import type { PlanView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { AttemptEditor } from "../../components/learning/AttemptEditor";
import { MicroLessonPanel } from "../../components/learning/MicroLessonPanel";
import { StatePanel } from "../../components/feedback/StatePanel";

/** 活动页：按 activity_kind 判别渲染题目、学习材料、微课堂（未接入时如实显示）。 */
export function ActivityPage() {
  const { planVersion = "", activityId = "" } = useParams();
  const { scopeKey, session } = useSession();
  const datasetId = scopeKey.datasetId;
  const studentId = session?.scope.student_id ?? null;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const plan = useQuery<PlanView>({
    queryKey: queryKeys.plan(scopeKey, studentId ?? "", null, planVersion),
    queryFn: () => getPlan(datasetId as string, studentId as string, { planVersion }),
    enabled: Boolean(datasetId && studentId && planVersion),
  });

  const activity = plan.data?.plan.recommendations.flatMap((item) => item.activities).find((item) => item.activity_id === activityId) ?? null;
  const back = () => navigate("/student/plan");

  if (!datasetId || !studentId) {
    return <StatePanel kind="empty" title="学生会话缺少数据集或学生身份" message="请回到身份入口重新选择。" />;
  }

  if (plan.isPending) return <StatePanel kind="loading" title="正在读取活动" />;

  if (plan.isError) {
    return (
      <div className="stack">
        <StatePanel
          kind={errorCode(plan.error) === "NOT_FOUND" ? "expired" : "error"}
          title={errorCode(plan.error) === "NOT_FOUND" ? "方案版本不存在或已过期" : "活动读取失败"}
          message={toErrorMessage(plan.error)}
          requestId={errorRequestId(plan.error)}
          action={<button type="button" className="btn" onClick={back}>返回学习计划</button>}
        />
      </div>
    );
  }

  if (!plan.data || !activity) {
    return (
      <StatePanel
        kind="expired"
        title="这个活动不属于当前方案版本"
        message="新方案发布后，未开始的旧活动会被拒绝；进行中的旧作答仍按原版本处理。"
        action={<button type="button" className="btn btnPrimary" onClick={back}>返回学习计划</button>}
      />
    );
  }

  const onChanged = () => {
    void queryClient.invalidateQueries({ queryKey: [scopeKey.mode, scopeKey.epoch] });
  };

  if (activity.activity_kind === "micro_lesson") {
    return (
      <MicroLessonPanel
        plan={plan.data.plan}
        planVersion={planVersion}
        activity={activity}
        onChanged={onChanged}
        onBack={back}
      />
    );
  }

  return (
    <AttemptEditor
      plan={plan.data.plan}
      planVersion={planVersion}
      activity={activity}
      onChanged={onChanged}
      onBack={back}
    />
  );
}

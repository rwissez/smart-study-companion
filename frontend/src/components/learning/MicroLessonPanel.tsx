import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { completeMicroLesson, getMicroLesson } from "../../api/service";
import type { MicroLessonView, PlanActivity, RecommendationBundle } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { StatePanel } from "../feedback/StatePanel";
import { LessonRenderer, validateLesson } from "./LessonRenderer";
import { IconCheck } from "../icons";

interface Props {
  plan: RecommendationBundle;
  planVersion: string;
  activity: PlanActivity;
  onChanged: () => void;
  onBack: () => void;
}

/** 微课堂：只读正式 LessonVersion，完成走专用接口；不生成题分。 */
export function MicroLessonPanel({ plan, planVersion, activity, onChanged, onBack }: Props) {
  const { scopeKey } = useSession();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; requestId: string | null } | null>(null);
  const [completed, setCompleted] = useState(activity.state === "completed");

  const contentVersion = activity.content_version ?? "unknown";
  const lesson = useQuery<MicroLessonView>({
    // content_version 参与缓存键：教师重生成微课堂后即使 plan_version 不变，
    // 学生再次打开也会按最新正文版本重新读取，不显示旧内容。
    queryKey: [...queryKeys.plan(scopeKey, plan.student_id, plan.node_id, planVersion), "lesson", activity.activity_id, contentVersion],
    queryFn: () => getMicroLesson(plan.run_id, plan.student_id, activity.activity_id, planVersion),
    staleTime: 0,
    refetchOnMount: "always",
  });

  const canComplete = lesson.data ? validateLesson(lesson.data).ok : false;

  const onComplete = async () => {
    if (!lesson.data) return;
    setBusy(true);
    setError(null);
    try {
      await completeMicroLesson(plan.run_id, plan.student_id, activity.activity_id, {
        plan_version: planVersion,
        content_version: lesson.data.content_version,
      });
      setCompleted(true);
      onChanged();
      void queryClient.invalidateQueries({ queryKey: [scopeKey.mode, scopeKey.epoch] });
    } catch (reason) {
      setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  if (lesson.isPending) return <StatePanel kind="loading" title="正在读取微课堂内容" />;
  if (lesson.isError) {
    return (
      <StatePanel
        kind={errorCode(lesson.error) === "NOT_FOUND" ? "expired" : "error"}
        title={errorCode(lesson.error) === "NOT_FOUND" ? "微课堂内容不存在或已过期" : "微课堂读取失败"}
        message={toErrorMessage(lesson.error)}
        requestId={errorRequestId(lesson.error)}
        onRetry={() => void lesson.refetch()}
      />
    );
  }

  return (
    <div className="stackLg">
      <div className="row">
        <button type="button" className="btn btnSm" onClick={onBack}>返回学习计划</button>
      </div>
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">{lesson.data.title || "微课堂"}</h1>
          <p className="pageSub">按正式学习方案生成 · 只读 · 完成只记录阅读事件</p>
        </div>
        <span className={`badge ${completed ? "badgeSuccess" : "badgeNeutral"}`}>
          {completed ? "已完成" : "阅读材料"}
        </span>
      </div>

      <LessonRenderer lesson={lesson.data} />

      {lesson.data.fallback_text && validateLesson(lesson.data).ok ? (
        <details>
          <summary className="small muted" style={{ cursor: "pointer" }}>文字版内容</summary>
          <p className="prose" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{lesson.data.fallback_text}</p>
        </details>
      ) : null}

      <div className="submitBar">
        <button type="button" className="btn btnPrimary" onClick={() => void onComplete()} disabled={busy || completed || !canComplete}>
          {completed ? <><IconCheck size={16} /> 已完成</> : busy ? "提交中…" : "完成微课堂"}
        </button>
        <span className="small muted">
          完成只记录阅读事件，不产生分数，也不推断掌握程度。
          {!canComplete ? " 当前内容未通过运行时校验，不能标记完成。" : ""}
        </span>
      </div>
      {error ? (
        <p className="fieldError" role="alert">
          {error.text}
          {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
        </p>
      ) : null}
      <p className="xs muted">内容以正式审核版本为准；页面只做安全渲染，不生成新的教学内容。</p>
    </div>
  );
}

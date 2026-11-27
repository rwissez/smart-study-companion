import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  decidePlanReview,
  getCatalog,
  getPlan,
  listLearningEvents,
  listPlanReviews,
} from "../../api/service";
import type {
  Catalog,
  PlanReviewDecisionResult,
  PlanReviewItem,
  RevisionWarning,
} from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { activityKindLabel, activityStateLabel, formatDate, questionTypeLabel } from "../../lib/format";
import { activityStatesFromEvents, mergeActivityState } from "../../lib/plan";
import { Modal } from "../ui/Modal";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { StatePanel } from "../feedback/StatePanel";
import { IconLock } from "../icons";

interface Props {
  open: boolean;
  studentId: string;
  nodeId: string;
  onClose: () => void;
  onPublished: () => void;
}

type Action = "approve" | "reject";

function candidateLine(item: PlanReviewItem): string {
  const changes = (item as unknown as { changes?: { goal?: string; activity_count?: number; estimated_minutes?: number } }).changes;
  const parts: string[] = [];
  if (changes?.goal) parts.push(`目标：${changes.goal}`);
  if (changes?.activity_count !== undefined) parts.push(`${changes.activity_count} 个活动`);
  if (changes?.estimated_minutes !== undefined) parts.push(`约 ${changes.estimated_minutes} 分钟`);
  if (item.summary) parts.push(item.summary);
  return parts.join(" · ") || "按当前学情重新生成的候选";
}

/** 学生画像→学习方案里的附属审核入口；只作用于当前学生。 */
export function PlanReviewDialog({ open, studentId, nodeId, onClose, onPublished }: Props) {
  const { scopeKey } = useSession();
  const datasetId = scopeKey.datasetId;
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<Action | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [goal, setGoal] = useState("");
  const [intro, setIntro] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; text: string; requestId: string | null; details: Array<{ field?: string; reason?: string }> } | null>(null);
  const [result, setResult] = useState<PlanReviewDecisionResult | null>(null);

  const reviews = useQuery({
    queryKey: queryKeys.planReviews(scopeKey, 1),
    queryFn: () => listPlanReviews(datasetId as string, { page: 1, pageSize: 100 }),
    enabled: open && Boolean(datasetId),
  });

  const candidates = useMemo(
    () => (reviews.data?.items ?? []).filter((item) => item.student_id === studentId),
    [reviews.data, studentId],
  );

  useEffect(() => {
    if (open && candidates.length === 1 && !result) setSelectedId(candidates[0].review_id);
  }, [open, candidates, result]);

  const selected = candidates.find((item) => item.review_id === selectedId) ?? null;

  const plan = useQuery({
    queryKey: queryKeys.plan(scopeKey, studentId, nodeId, null),
    queryFn: () => getPlan(datasetId as string, studentId, { nodeId }),
    enabled: open && Boolean(datasetId && studentId),
  });

  const events = useQuery({
    queryKey: queryKeys.learningEvents(scopeKey, studentId),
    queryFn: () => listLearningEvents(datasetId as string, studentId, { pageSize: 200 }),
    enabled: open && Boolean(datasetId && studentId),
  });

  const catalog = useQuery<Catalog>({
    queryKey: queryKeys.catalog(scopeKey, plan.data?.plan.catalog_version ?? null),
    queryFn: () => getCatalog(plan.data?.plan.catalog_version ?? null),
    enabled: open && Boolean(plan.data),
  });

  const materialTypes = useMemo(() => {
    const map: Record<string, string | null> = {};
    (catalog.data?.materials ?? []).forEach((material) => {
      map[material.material_id] = material.question_type;
    });
    return map;
  }, [catalog.data]);

  const derived = useMemo(
    () => (plan.data ? activityStatesFromEvents(events.data?.items ?? [], plan.data.plan_version) : {}),
    [events.data, plan.data],
  );

  const activities = plan.data?.plan.recommendations.flatMap((item) => item.activities) ?? [];
  const completedIds = new Set(
    activities.filter((activity) => mergeActivityState(activity, derived) === "completed").map((activity) => activity.activity_id),
  );

  const reset = () => {
    setReason("");
    setGoal("");
    setIntro("");
    setConfirmAction(null);
    setEditOpen(false);
    setError(null);
  };

  const decide = async (action: "approve" | "reject" | "edit") => {
    if (!selected) return;
    if ((action === "reject" || action === "edit") && !reason.trim()) {
      setError({ code: "VALIDATION_ERROR", text: action === "reject" ? "驳回需要填写理由。" : "修改需要填写理由。", requestId: null, details: [] });
      return;
    }
    if (action === "edit" && !goal.trim() && !intro.trim()) {
      setError({ code: "VALIDATION_ERROR", text: "至少填写一项修改内容。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await decidePlanReview(datasetId as string, selected.review_id, selected.node_id || nodeId, {
        decision: action,
        reason: reason.trim(),
        basePlanVersion: selected.plan_version,
        changes:
          action === "edit"
            ? {
                ...(goal.trim() ? { goal: goal.trim() } : {}),
                ...(intro.trim() ? { student_intro: intro.trim() } : {}),
              }
            : null,
      });
      setResult(response);
      reset();
      setSelectedId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.planReviews(scopeKey, 1) });
      await queryClient.invalidateQueries({ queryKey: [scopeKey.mode, scopeKey.epoch] });
      await reviews.refetch();
      await plan.refetch();
      onPublished();
    } catch (reasonError) {
      const code = errorCode(reasonError);
      setError({
        code,
        text:
          code === "CONFLICT" || code === "REVISION_CONFLICT"
            ? "候选已被处理或已过期，已为你刷新列表。"
            : toErrorMessage(reasonError),
        requestId: errorRequestId(reasonError),
        details: (reasonError as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
      });
      if (code === "CONFLICT" || code === "REVISION_CONFLICT") await reviews.refetch();
    } finally {
      setBusy(false);
    }
  };

  const hardBlocked = Boolean((selected as unknown as { precheck?: { hard_block?: boolean; reason?: string } })?.precheck?.hard_block);

  return (
    <>
      <Modal
        open={open}
        title="方案审核"
        description={`学生 ${studentId} · 候选来自教师对话中的生成操作，只作用于当前学生`}
        onClose={onClose}
        wide
        footer={<button type="button" className="btn" onClick={onClose}>关闭</button>}
      >
        <div className="stack">
          {result ? (
            <div className="statePanel" role="status">
              <div className="rowBetween">
                <span className="stateTitle">
                  {result.review_status === "rejected"
                    ? "已驳回候选，正式方案不变"
                    : result.preview_id
                      ? "已生成修改预览，等待再次审核"
                      : "已通过并发布"}
                </span>
                <button type="button" className="btn btnSm" onClick={() => setResult(null)}>收起</button>
              </div>
              <dl className="kv">
                {result.plan_version ? <><dt>新方案版本</dt><dd>{result.plan_version}</dd></> : null}
                {result.preview_id ? <><dt>预览</dt><dd>已生成新的候选预览（未发布）</dd></> : null}
                {result.publication_status ? (
                  <><dt>发布状态</dt><dd>{result.publication_status === "published" ? "已发布" : result.publication_status}</dd></>
                ) : null}
                <dt>审核状态</dt>
                <dd>{result.review_status === "validated" || result.review_status === "human_confirmed" ? "已通过" : result.review_status === "rejected" ? "已驳回" : result.review_status}</dd>
              </dl>
              {result.warnings?.length ? (
                <ul>{result.warnings.map((warning, index) => <li key={index} className="small">{warning.message}</li>)}</ul>
              ) : null}
            </div>
          ) : null}

          {error ? (
            <div className="statePanel stateError" role="alert">
              <span className="stateTitle">审核未完成（{error.code}）</span>
              <span>{error.text}</span>
              {error.requestId ? <span className="xs">请求编号：{error.requestId}</span> : null}
              {error.details.length ? <ul>{error.details.map((detail, index) => <li key={index} className="small">{detail.field ? `${detail.field}：` : ""}{detail.reason}</li>)}</ul> : null}
            </div>
          ) : null}

          {reviews.isPending ? <StatePanel kind="loading" title="正在读取候选" /> : null}
          {reviews.isError ? <StatePanel kind="error" title="候选读取失败" message={toErrorMessage(reviews.error)} /> : null}

          {reviews.data && candidates.length === 0 ? (
            <StatePanel
              kind="empty"
              title="当前学生没有待审核候选"
              message="候选来自教师对话中的生成操作：从学生画像进入 AI 对话，点击“生成方案候选”但不确认，候选就会出现在这里。"
            />
          ) : null}

          {candidates.length > 0 ? (
            <div className="tableWrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">候选</th>
                    <th scope="col">基础方案</th>
                    <th scope="col" className="numeric">预计用时</th>
                    <th scope="col" className="numeric">警告</th>
                    <th scope="col">过期时间</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((item) => {
                    const blocked = Boolean((item as unknown as { precheck?: { hard_block?: boolean } }).precheck?.hard_block);
                    return (
                      <tr
                        key={item.review_id}
                        onClick={() => { setSelectedId(item.review_id); setResult(null); setError(null); }}
                        style={{ cursor: "pointer", background: selectedId === item.review_id ? "var(--color-selected-bg)" : undefined }}
                      >
                        <td className="small">
                          {candidateLine(item)}
                          {blocked ? <div className="xs fieldError">硬门未通过</div> : null}
                        </td>
                        <td className="small muted">{item.plan_version}</td>
                        <td className="numeric">{item.estimated_minutes ?? "—"} 分钟</td>
                        <td className="numeric">{item.warnings?.length ?? 0}</td>
                        <td className="small">{formatDate(item.expires_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}

          {selected ? (
            <div className="editorGrid">
              <section className="stack">
                <div className="sectionHead">
                  <h3 className="sectionTitle">当前正式方案</h3>
                  <span className="sectionHint">来自正式版本，只读</span>
                </div>
                {plan.isPending ? <StatePanel kind="loading" title="正在读取当前方案" /> : null}
                {plan.isError ? <StatePanel kind="error" title="当前方案读取失败" message={toErrorMessage(plan.error)} /> : null}
                {plan.data ? (
                  <>
                    <p className="prose">{plan.data.plan.recommendations[0]?.goal ?? "当前方案没有目标文本"}</p>
                    <p className="small muted">{plan.data.plan.recommendations[0]?.student_intro}</p>
                    <div className="editorActivityList">
                      {activities.map((activity, index) => {
                        const state = mergeActivityState(activity, derived);
                        const locked = state === "completed" || state === "in_progress";
                        return (
                          <div className="editorActivity" key={`${activity.activity_id}-${index}`}>
                            <div className="activityIndex">{index + 1}</div>
                            <div className="activityMain">
                              <div className="activityTitle">{activity.title || activity.purpose || "未命名活动"}</div>
                              <div className="activityMeta">
                                <span className="tag">{activityKindLabel(activity.activity_kind)}</span>
                                {activity.material_id ? <span>{questionTypeLabel(materialTypes[activity.material_id] ?? null)}</span> : null}
                                <span>{activity.estimate_minutes} 分钟</span>
                                <span>{activityStateLabel(state)}</span>
                                {locked ? <span className="tag tagWarning"><IconLock size={12} /> 已完成/进行中</span> : null}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                ) : null}
              </section>

              <section className="stack">
                <div className="sectionHead">
                  <h3 className="sectionTitle">候选变更摘要</h3>
                  <span className="sectionHint">确认后才替换正式方案</span>
                </div>
                <dl className="kv">
                  <dt>候选摘要</dt><dd>{candidateLine(selected)}</dd>
                  <dt>基础版本</dt><dd className="breakAnywhere">{selected.plan_version}</dd>
                  <dt>预计用时</dt><dd>{selected.estimated_minutes ?? "—"} 分钟</dd>
                  <dt>警告</dt><dd>{selected.warnings?.length ?? 0} 条</dd>
                  <dt>过期时间</dt><dd>{formatDate(selected.expires_at)}</dd>
                  <dt>已完成活动</dt><dd>{completedIds.size} 个（硬门不允许移除）</dd>
                </dl>
                {hardBlocked ? (
                  <StatePanel
                    kind="not-ready"
                    title="硬门预检未通过，不能通过"
                    message={(selected as unknown as { precheck?: { reason?: string } }).precheck?.reason || "该候选会移除已完成或进行中的活动。"}
                  />
                ) : null}
                {selected.warnings?.length ? (
                  <details>
                    <summary className="small muted">查看候选警告</summary>
                    <ul>{selected.warnings.map((warning: RevisionWarning, index: number) => <li key={index} className="small">{warning.message}</li>)}</ul>
                  </details>
                ) : null}
                <div className="row">
                  <button
                    type="button"
                    className="btn btnPrimary"
                    disabled={busy || hardBlocked}
                    title={hardBlocked ? "硬门预检未通过" : "通过并发布"}
                    onClick={() => { setError(null); setReason(""); setConfirmAction("approve"); }}
                  >
                    通过
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => { setError(null); setReason(""); setConfirmAction("reject"); }}
                  >
                    驳回
                  </button>
                  <button type="button" className="btn" disabled={busy} onClick={() => { setError(null); setReason(""); setGoal(""); setIntro(""); setEditOpen(true); }}>
                    修改后预览
                  </button>
                </div>
              </section>
            </div>
          ) : null}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction === "approve" ? "确认通过并发布候选方案" : "确认驳回候选方案"}
        confirmLabel={confirmAction === "approve" ? "确认通过" : "确认驳回"}
        danger={confirmAction === "reject"}
        busy={busy}
        onConfirm={() => void decide(confirmAction ?? "approve")}
        onCancel={() => { setConfirmAction(null); setError(null); }}
      >
        <p>{confirmAction === "approve" ? `学生会读到新方案，原方案保留历史。` : "驳回只记录审核决定，不改变学生当前方案。"}</p>
        <label className="field">
          <span className="fieldLabel">审核理由{confirmAction === "reject" ? "（必填）" : "（可选）"}</span>
          <textarea className="textarea" style={{ minHeight: 80 }} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
        </label>
        {error ? <p className="fieldError" role="alert">{error.text}</p> : null}
      </ConfirmDialog>

      <Modal
        open={editOpen}
        title="修改候选并生成预览"
        description="修改后只生成预览，不直接发布"
        onClose={() => { setEditOpen(false); setError(null); }}
        footer={
          <>
            <button type="button" className="btn" onClick={() => setEditOpen(false)} disabled={busy}>取消</button>
            <button type="button" className="btn btnPrimary" onClick={() => void decide("edit")} disabled={busy}>
              {busy ? "提交中…" : "生成修改预览"}
            </button>
          </>
        }
      >
        <div className="stack">
          <label className="field"><span className="fieldLabel">学习目标</span><textarea className="textarea" style={{ minHeight: 80 }} value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="留空表示不修改" /></label>
          <label className="field"><span className="fieldLabel">学生开场白</span><textarea className="textarea" style={{ minHeight: 80 }} value={intro} onChange={(event) => setIntro(event.target.value)} placeholder="留空表示不修改" /></label>
          <label className="field"><span className="fieldLabel">修改理由（必填）</span><textarea className="textarea" style={{ minHeight: 80 }} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} /></label>
          {error ? <p className="fieldError" role="alert">{error.text}</p> : null}
        </div>
      </Modal>
    </>
  );
}

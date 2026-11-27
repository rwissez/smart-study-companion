import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  getPlan,
  listLearningEvents,
  listRevisions,
  optimizePlanRevision,
  previewRevision,
  publishRevision,
  searchMaterials,
} from "../../api/service";
import type {
  ActivityState,
  MaterialView,
  PlanActivity,
  PlanOptimizeChange,
  PlanOptimizeResult,
  PlanOptimizeStatus,
  PlanView,
  RevisionChanges,
  RevisionPreview,
  RevisionWarning,
} from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { activityKindLabel, formatDate, questionTypeLabel } from "../../lib/format";
import { activityStatesFromEvents, mergeActivityState } from "../../lib/plan";
import { Modal } from "../ui/Modal";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { StatePanel } from "../feedback/StatePanel";
import { IconArrowRight, IconLock } from "../icons";

interface Props {
  open: boolean;
  studentId: string;
  nodeId: string;
  onClose: () => void;
  onPublished: () => void;
}

const DEFAULT_HINTS = [
  "先回忆这道题涉及的定义和适用条件，不必急着算。",
  "写出你想到的第一步，再检查符号、括号或移项。",
  "对照课本同类例题的解法，逐步检查最后两步。",
];

function severityLabel(severity: string): string {
  return severity === "error" ? "硬门" : severity === "warning" ? "教学警告" : "提示";
}

const OPTIMIZE_STATUS_LABEL: Record<PlanOptimizeStatus, string> = {
  proposal: "已生成候选建议（只读，不发布）",
  unsatisfiable: "现有候选无法满足这条意图",
  rejected: "模型建议未通过程序校验，已整份拒绝",
  unavailable: "模型不可用，保留当前方案",
};

const OPTIMIZE_OP_LABEL: Record<PlanOptimizeChange["op"], string> = {
  add: "增加",
  remove: "移除",
  replace: "替换",
  reorder: "调序",
};

function optimizeSourceLabel(source: string | null): string {
  if (source === "current_plan") return "当前方案";
  if (source === "catalog") return "正式目录候选";
  return "未标注来源";
}

/** 教师学习方案编辑：改文字 / 调活动，检查并发布走 preview → 确认 → publish。 */
export function PlanEditor({ open, studentId, nodeId, onClose, onPublished }: Props) {
  const { scopeKey } = useSession();
  const datasetId = scopeKey.datasetId;
  const queryClient = useQueryClient();
  const [goal, setGoal] = useState("");
  const [reasonText, setReasonText] = useState("");
  const [intro, setIntro] = useState("");
  const [activities, setActivities] = useState<PlanActivity[]>([]);
  const [activityDirty, setActivityDirty] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [revisionReason, setRevisionReason] = useState("");
  const [preview, setPreview] = useState<RevisionPreview | null>(null);
  const [ackWarnings, setAckWarnings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; requestId: string | null; details: Array<{ field?: string; reason?: string }> } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [published, setPublished] = useState<{ planVersion: string; revisionId: string; publicationStatus: string } | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [optimizeIntent, setOptimizeIntent] = useState("");
  const [optimizeResult, setOptimizeResult] = useState<PlanOptimizeResult | null>(null);
  const [optimizeBusy, setOptimizeBusy] = useState(false);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);

  const plan = useQuery<PlanView>({
    queryKey: queryKeys.plan(scopeKey, studentId, nodeId, null),
    queryFn: () => getPlan(datasetId as string, studentId, { nodeId }),
    enabled: open && Boolean(datasetId && studentId && nodeId),
  });

  const events = useQuery({
    queryKey: queryKeys.learningEvents(scopeKey, studentId),
    queryFn: () => listLearningEvents(datasetId as string, studentId, { pageSize: 200 }),
    enabled: open && Boolean(datasetId && studentId),
  });

  const revisions = useQuery({
    queryKey: queryKeys.revisions(scopeKey, plan.data?.plan.run_id ?? "", studentId, nodeId),
    queryFn: () => listRevisions(plan.data!.plan.run_id, studentId, nodeId),
    enabled: open && Boolean(plan.data?.plan.run_id),
  });

  const materials = useQuery({
    queryKey: [...queryKeys.materials(scopeKey, plan.data?.plan.catalog_version ?? null), searchText],
    queryFn: () =>
      searchMaterials(datasetId as string, {
        catalogVersion: plan.data?.plan.catalog_version ?? null,
        q: searchText.trim() || null,
        pageSize: 20,
      }),
    enabled: open && searchOpen && Boolean(datasetId),
  });

  const derived = useMemo(
    () => (plan.data ? activityStatesFromEvents(events.data?.items ?? [], plan.data.plan_version) : {}),
    [events.data, plan.data],
  );

  const resetDraft = (data: PlanView) => {
    const first = data.plan.recommendations[0];
    setGoal(first?.goal ?? "");
    setReasonText(first?.reason ?? "");
    setIntro(first?.student_intro ?? "");
    setActivities(first?.activities.map((activity) => ({ ...activity, hint_levels: [...activity.hint_levels] })) ?? []);
    setActivityDirty(false);
    setDirty(false);
    setRevisionReason("");
    setPreview(null);
    setAckWarnings(false);
    setError(null);
    // 上一份方案的优化建议不能带到新版本上。
    setOptimizeIntent("");
    setOptimizeResult(null);
    setOptimizeError(null);
  };

  useEffect(() => {
    // 每次重新打开都从当前正式版本开始；上一次的成功面板不能带到下一次编辑。
    if (open) setPublished(null);
  }, [open]);

  useEffect(() => {
    if (open && plan.data) resetDraft(plan.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, plan.data?.plan_version]);

  const invalidatePreview = () => {
    setPreview(null);
    setAckWarnings(false);
  };

  const stateOf = (activity: PlanActivity): ActivityState => mergeActivityState(activity, derived);
  const isLocked = (activity: PlanActivity): boolean => {
    const state = stateOf(activity);
    return state === "completed" || state === "in_progress" || state === "cancelled";
  };

  const markActivityDirty = (next: PlanActivity[]) => {
    setActivities(next);
    setActivityDirty(true);
    setDirty(true);
    invalidatePreview();
  };

  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= activities.length) return;
    if (isLocked(activities[index]) || isLocked(activities[target])) return;
    const next = [...activities];
    [next[index], next[target]] = [next[target], next[index]];
    markActivityDirty(next.map((activity, order) => ({ ...activity, order: order + 1 })));
  };

  const remove = (index: number) => {
    if (isLocked(activities[index])) return;
    markActivityDirty(activities.filter((_, position) => position !== index).map((activity, order) => ({ ...activity, order: order + 1 })));
  };

  const addMaterial = (material: MaterialView) => {
    const nextOrder = activities.length + 1;
    const isQuestion = material.material_type === "question" && material.question_type !== null;
    const activity: PlanActivity = {
      order: nextOrder,
      material_id: material.material_id,
      purpose: "教师补充材料",
      hint_levels: isQuestion ? [...DEFAULT_HINTS] : [],
      observation_points: [],
      previously_attempted: false,
      activity_id: "",
      group_id: activities[0]?.group_id ?? "",
      knowledge_ids: [...material.indicator_ids],
      activity_kind: isQuestion ? "question" : "study_material",
      state: "not_started",
      title: material.source_label || material.content.slice(0, 24),
      expected_outcome: "",
      estimate_minutes: 4,
      dependencies: [],
      evidence_refs: [],
      exposure_mode: "practice",
      repeat_reason: null,
      content_ref: null,
      content_version: null,
      source_label: material.source_label,
    };
    markActivityDirty([...activities, activity]);
    setSearchOpen(false);
    setSearchText("");
  };

  const updateHints = (index: number, level: number, value: string) => {
    const next = activities.map((activity, position) => {
      if (position !== index) return activity;
      const hints = [...activity.hint_levels];
      while (hints.length < 3) hints.push("");
      hints[level] = value;
      return { ...activity, hint_levels: hints };
    });
    markActivityDirty(next);
  };

  const buildChanges = (): RevisionChanges => {
    const changes: RevisionChanges = {
      goal: goal || undefined,
      reason: reasonText || undefined,
      student_intro: intro,
    };
    if (activityDirty) {
      changes.activities = activities.map((activity, index) => ({ ...activity, order: index + 1 }));
    }
    return changes;
  };

  const requestPreview = async () => {
    if (!plan.data) return;
    if (!revisionReason.trim()) {
      setError({ text: "请填写修改理由，便于审核与追溯。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await previewRevision(plan.data.plan.run_id, studentId, {
        nodeId,
        basePlanVersion: plan.data.plan_version,
        reason: revisionReason.trim(),
        changes: buildChanges(),
      });
      setPreview(result);
      setAckWarnings(false);
    } catch (reason) {
      const code = errorCode(reason);
      if (code === "REVISION_CONFLICT" || code === "CONFLICT") {
        setNotice("方案已更新，已为你刷新，请重新检查草稿。");
        await queryClient.invalidateQueries({ queryKey: queryKeys.plan(scopeKey, studentId, nodeId, null) });
        await plan.refetch();
      } else {
        setError({
          text: toErrorMessage(reason),
          requestId: errorRequestId(reason),
          details: (reason as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const requestOptimize = async () => {
    if (!plan.data) return;
    const intent = optimizeIntent.trim();
    if (!intent) {
      setOptimizeError("请先写下想怎么改，例如：减轻书写负担，把重复练习换成一道独立检查题。");
      return;
    }
    setOptimizeBusy(true);
    setOptimizeError(null);
    setNotice(null);
    try {
      const result = await optimizePlanRevision(plan.data.plan.run_id, studentId, {
        nodeId,
        basePlanVersion: plan.data.plan_version,
        intent,
      });
      setOptimizeResult(result);
    } catch (reason) {
      const code = errorCode(reason);
      if (code === "REVISION_CONFLICT" || code === "CONFLICT") {
        setNotice("方案已更新，已为你刷新；请重新填写意图再生成建议。");
        setOptimizeResult(null);
        await queryClient.invalidateQueries({ queryKey: queryKeys.plan(scopeKey, studentId, nodeId, null) });
        await plan.refetch();
      } else {
        setOptimizeError(toErrorMessage(reason));
      }
    } finally {
      setOptimizeBusy(false);
    }
  };

  /** 只有在建议能由现有活动直接表达时才可带入草稿；带入后仍要重新检查草稿。 */
  const applyOptimizeDraft = () => {
    const suggested = optimizeResult?.revision_changes?.activities;
    if (!suggested || suggested.length === 0) return;
    markActivityDirty(
      suggested.map((activity, index) => ({
        ...activity,
        order: index + 1,
        hint_levels: [...activity.hint_levels],
      })),
    );
    if (!revisionReason.trim()) setRevisionReason(optimizeIntent.trim().slice(0, 500));
    setNotice("已把可套用的调整带入草稿；请先做“检查草稿（第一步）”，通过后再确认发布。");
  };

  const doPublish = async () => {
    if (!plan.data || !preview) return;
    if (preview.warnings.some((warning) => warning.severity === "warning") && !ackWarnings) {
      setError({ text: "请先勾选确认教学警告，再发布。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await publishRevision(
        plan.data.plan.run_id,
        studentId,
        preview.preview_id,
        nodeId,
        preview.warning_digest,
      );
      setPublished({
        planVersion: result.plan_version,
        revisionId: result.revision_id,
        publicationStatus: result.publication_status,
      });
      setPreview(null);
      setDirty(false);
      await queryClient.invalidateQueries({ queryKey: [scopeKey.mode, scopeKey.epoch] });
      await plan.refetch();
      onPublished();
    } catch (reason) {
      const code = errorCode(reason);
      if (code === "REVISION_CONFLICT" || code === "CONFLICT") {
        setNotice("方案已更新，已为你刷新，请重新检查并发布。");
        setPreview(null);
        await plan.refetch();
      } else {
        setError({
          text: toErrorMessage(reason),
          requestId: errorRequestId(reason),
          details: (reason as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    if (dirty || preview) {
      setConfirmClose(true);
      return;
    }
    onClose();
  };

  const warnings: RevisionWarning[] = preview?.warnings ?? [];

  return (
    <>
      <Modal
        open={open}
        title="编辑学习方案"
        description={plan.data ? `学生 ${studentId} · 当前观察节点 · 基于当前正式版本` : "正在读取当前方案"}
        onClose={close}
        wide
        footer={
          published ? (
            <>
              <button type="button" className="btn" onClick={onClose}>关闭并刷新</button>
              <button
                type="button"
                className="btn btnPrimary"
                onClick={() => {
                  setPublished(null);
                  if (plan.data) resetDraft(plan.data);
                }}
              >
                继续编辑新版本
              </button>
            </>
          ) : preview ? (
            <>
              <button type="button" className="btn" onClick={() => invalidatePreview()} disabled={busy}>返回修改</button>
              <button type="button" className="btn btnPrimary" onClick={() => void doPublish()} disabled={busy}>
                {busy ? "发布中…" : "确认发布（第二步）"}
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn" onClick={close} disabled={busy}>取消</button>
              <button type="button" className="btn btnPrimary" onClick={() => void requestPreview()} disabled={busy || !plan.data}>
                {busy ? "检查中…" : "检查草稿（第一步）"}
              </button>
            </>
          )
        }
      >
        {plan.isPending ? <StatePanel kind="loading" title="正在读取正式方案" /> : null}
        {plan.isError ? (
          <StatePanel kind="error" title="方案读取失败" message={toErrorMessage(plan.error)} requestId={errorRequestId(plan.error)} />
        ) : null}

        {published ? (
          <div className="stack">
            <StatePanel
              kind="empty"
              title="方案已发布"
              message="新版本已切换为正式方案，原方案保留历史；已完成与进行中的活动不受影响。"
            />
            <dl className="kv">
              <dt>新版本</dt><dd>{published.planVersion ? "已生成新的正式版本" : "服务端未返回版本"}</dd>
              <dt>审核状态</dt><dd>教师确认（human_confirmed）</dd>
              <dt>发布状态</dt><dd>{published.publicationStatus === "published" ? "已发布" : "发布状态待确认"}</dd>
              <dt>修订编号</dt><dd>{published.revisionId ? "已记录" : "未返回"}</dd>
            </dl>
            <p className="small muted">学生端刷新后会读到新方案；旧版历史可在修订记录中查看。</p>
          </div>
        ) : null}

        {plan.data && !published ? (
          <div className="stack">
            <p className={`stepsHint${preview ? " stepsHintActive" : ""}`} role="status">
              ① 修改草稿 → ② 检查草稿（只生成预览，不会发布） → ③ 确认发布
              {preview ? " · 当前：检查已通过，等待确认发布" : ""}
            </p>
            <div className="editorGrid">
            <div className="stack">
              <div className="sectionHead">
                <h3 className="sectionTitle">按意图优化</h3>
              </div>
              <p className="small muted">
                写下想怎么改，系统只在当前方案与正式候选里给只读建议；不会直接改方案，也不会发布。
              </p>
              <label className="field">
                <span className="fieldLabel">教师修改意图</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 72 }}
                  value={optimizeIntent}
                  maxLength={500}
                  placeholder="例如：减轻书写负担，把重复练习换成一道独立检查题"
                  onChange={(event) => { setOptimizeIntent(event.target.value); setOptimizeError(null); }}
                />
              </label>
              <button
                type="button"
                className="btn btnSm"
                onClick={() => void requestOptimize()}
                disabled={optimizeBusy || busy || !optimizeIntent.trim()}
              >
                {optimizeBusy ? "生成中…" : "生成优化建议"}
              </button>
              {optimizeError ? <p className="small fieldError" role="alert">{optimizeError}</p> : null}
              {optimizeResult ? (
                <div className="statePanel" role="status">
                  <span className="stateTitle">{OPTIMIZE_STATUS_LABEL[optimizeResult.status]}</span>
                  {optimizeResult.reason ? <span className="small">{optimizeResult.reason}</span> : null}
                  <span className="xs muted">
                    候选池 {optimizeResult.candidate_count} 道题 · {optimizeResult.pool_note} ·{" "}
                    {optimizeResult.model_used ? "本次由模型生成" : "本次未使用模型"}
                  </span>
                  {optimizeResult.changes.length ? (
                    <ul>
                      {optimizeResult.changes.map((change, index) => {
                        const title = change.activity_title ?? change.activity_key ?? "未命名活动";
                        const from = change.from_activity_title ?? change.from_activity_key;
                        return (
                          <li key={`${change.op}-${index}`} className="small">
                            <strong>{OPTIMIZE_OP_LABEL[change.op]}</strong>
                            {` · ${change.group_key}`}
                            {change.group_goal ? `（${change.group_goal}）` : ""}
                            {change.op === "replace" && from ? `：由「${from}」换成「${title}」` : `：${title}`}
                            {change.position ? `，目标位置第 ${change.position} 位` : ""}
                            {` · 来源：${optimizeSourceLabel(change.activity_source)}`}
                            <div className="xs muted">{change.reason}</div>
                            {change.apply_blocker ? <div className="xs muted">{change.apply_blocker}</div> : null}
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                  {optimizeResult.limitations.length ? (
                    <ul>
                      {optimizeResult.limitations.map((item, index) => (
                        <li key={`limitation-${index}`} className="xs muted">{item}</li>
                      ))}
                    </ul>
                  ) : null}
                  {optimizeResult.blocked_reasons.length ? (
                    <ul>
                      {optimizeResult.blocked_reasons.map((item, index) => (
                        <li key={`blocked-${index}`} className="xs muted">{item}</li>
                      ))}
                    </ul>
                  ) : null}
                  {optimizeResult.issues.length ? (
                    <ul>
                      {optimizeResult.issues.map((issue, index) => (
                        <li key={`issue-${index}`} className="xs">
                          <strong>{issue.severity === "error" ? "硬门" : "问题"}</strong>：{issue.message}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <span className="xs muted">{optimizeResult.preview_note}</span>
                  {optimizeResult.revision_changes?.activities?.length ? (
                    <button type="button" className="btn btnSm" onClick={applyOptimizeDraft} disabled={busy}>
                      把可套用的调整带入草稿（仍需检查）
                    </button>
                  ) : null}
                </div>
              ) : null}

              <h3 className="paneTitle">方案文字</h3>
              <label className="field">
                <span className="fieldLabel">学习目标</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 90 }}
                  value={goal}
                  onChange={(event) => { setGoal(event.target.value); setDirty(true); invalidatePreview(); }}
                />
              </label>
              <label className="field">
                <span className="fieldLabel">教师说明 / 修改理由（会随修订保存）</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 90 }}
                  value={reasonText}
                  onChange={(event) => { setReasonText(event.target.value); setDirty(true); invalidatePreview(); }}
                />
              </label>
              <label className="field">
                <span className="fieldLabel">学生开场白</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 90 }}
                  value={intro}
                  onChange={(event) => { setIntro(event.target.value); setDirty(true); invalidatePreview(); }}
                />
              </label>
              <label className="field">
                <span className="fieldLabel">本次修订理由（必填，用于审核追溯）</span>
                <input
                  className="input"
                  value={revisionReason}
                  maxLength={500}
                  onChange={(event) => { setRevisionReason(event.target.value); invalidatePreview(); }}
                  placeholder="例如：学生反馈开场白太长，压缩后重新安排"
                />
              </label>

              <div className="sectionHead" style={{ marginTop: 8 }}>
                <h3 className="sectionTitle">活动列表</h3>
                <button type="button" className="btn btnSm" onClick={() => setSearchOpen((value) => !value)}>
                  {searchOpen ? "收起题库" : "从题库添加"}
                </button>
              </div>

              {activities.some((activity) => activity.activity_kind === "micro_lesson") ? (
                <p className="small muted">
                  当前方案含微课堂活动。服务端重建活动列表要求每个活动都有材料编号，调整活动可能返回 MATERIAL_INVALID；只改文字不受影响。
                </p>
              ) : null}

              {searchOpen ? (
                <div className="materialSearch">
                  <label className="field">
                    <span className="fieldLabel">搜索题库（题干或编号）</span>
                    <input
                      className="input"
                      value={searchText}
                      onChange={(event) => setSearchText(event.target.value)}
                      placeholder="输入关键词，例如 方程"
                    />
                  </label>
                  {materials.isPending ? <p className="small muted">正在检索…</p> : null}
                  {materials.isError ? <p className="small fieldError">题库检索失败：{toErrorMessage(materials.error)}</p> : null}
                  <div className="materialList">
                    {(materials.data?.items ?? []).map((material) => (
                      <div className="materialItem" key={material.material_id}>
                        <div className="activityMain">
                          <div className="small">{material.content.slice(0, 60)}</div>
                          <div className="xs muted">
                            {questionTypeLabel(material.question_type)} · {activityKindLabel(material.material_type === "question" ? "question" : "study_material")} · {material.source_label ?? "来源未标注"}
                          </div>
                        </div>
                        <button type="button" className="btn btnSm" onClick={() => addMaterial(material)}>加入</button>
                      </div>
                    ))}
                    {materials.data && materials.data.items.length === 0 ? <p className="small muted">没有匹配的正式材料。</p> : null}
                  </div>
                </div>
              ) : null}

              <div className="editorActivityList">
                {activities.map((activity, index) => {
                  const locked = isLocked(activity);
                  return (
                    <div className="editorActivity" key={`${activity.activity_id || "new"}-${index}`}>
                      <div className="activityIndex">{index + 1}</div>
                      <div className="activityMain">
                        <div className="activityTitle">{activity.title || activity.purpose || "未命名活动"}</div>
                        <div className="activityMeta">
                          <span className="tag">{activityKindLabel(activity.activity_kind)}</span>
                          <span>{locked ? (stateOf(activity) === "completed" ? "已完成（锁定）" : "进行中（锁定）") : "未开始"}</span>
                          <span>{activity.estimate_minutes} 分钟</span>
                        </div>
                        {activity.activity_kind === "question" ? (
                          <details className="hintEditor">
                            <summary className="xs muted">编辑三级提示（{activity.hint_levels.length} 条）</summary>
                            <div className="stackSm" style={{ marginTop: 6 }}>
                              {[0, 1, 2].map((level) => (
                                <textarea
                                  key={level}
                                  className="textarea"
                                  style={{ minHeight: 56 }}
                                  value={activity.hint_levels[level] ?? ""}
                                  placeholder={`第 ${level + 1} 级提示`}
                                  onChange={(event) => updateHints(index, level, event.target.value)}
                                />
                              ))}
                            </div>
                          </details>
                        ) : null}
                      </div>
                      <div className="editorActions">
                        <button type="button" className="btn btnSm" disabled={locked || index === 0} onClick={() => move(index, -1)} aria-label="上移">↑</button>
                        <button type="button" className="btn btnSm" disabled={locked || index === activities.length - 1} onClick={() => move(index, 1)} aria-label="下移">↓</button>
                        <button type="button" className="btn btnSm" disabled={locked} onClick={() => remove(index)}>移除</button>
                        {locked ? <span className="activityLocked"><IconLock size={14} /> 锁定</span> : null}
                      </div>
                    </div>
                  );
                })}
                {activities.length === 0 ? <p className="small muted">当前没有可编辑的活动。</p> : null}
              </div>
            </div>

            <aside className="stack">
              <div className="sectionHead">
                <h3 className="sectionTitle">检查与历史</h3>
              </div>
              {notice ? <p className="small" role="status">{notice}</p> : null}
              {error ? (
                <div className="statePanel stateError" role="alert">
                  <span className="stateTitle">{error.text}</span>
                  {error.requestId ? <span className="xs">请求编号：{error.requestId}</span> : null}
                  {error.details.length ? (
                    <ul>
                      {error.details.map((detail, index) => (
                        <li key={index} className="small">{detail.field ? `${detail.field}：` : ""}{detail.reason ?? ""}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}

              {preview ? (
                <div className="statePanel">
                  <span className="stateTitle">检查通过，等待确认</span>
                  <span className="small">过期时间 {formatDate(preview.expires_at)}</span>
                  <span className="small">{warnings.length ? `${warnings.length} 条教学警告` : "没有教学警告"}</span>
                  {warnings.length ? (
                    <ul>
                      {warnings.map((warning, index) => (
                        <li key={index} className="small">
                          <strong>{severityLabel(warning.severity)}</strong>：{warning.message}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {warnings.some((warning) => warning.severity === "warning") ? (
                    <label className="checkRow">
                      <input type="checkbox" checked={ackWarnings} onChange={(event) => setAckWarnings(event.target.checked)} />
                      我已阅读并确认这些教学警告
                    </label>
                  ) : null}
                  <span className="xs muted">确认发布会写入教师确认审核记录（human_confirmed），不会自动重放旧预览。</span>
                </div>
              ) : (
                <p className="small muted">点击“检查并发布”先做预览；服务端会检查材料、提示泄答、负担与已完成活动保护。</p>
              )}

              <div>
                <h3 className="paneTitle">历史修订</h3>
                {revisions.isPending ? <p className="small muted">正在读取…</p> : null}
                {revisions.data && revisions.data.items.length === 0 ? <p className="small muted">还没有教师修订记录。</p> : null}
                <ul className="sourceList">
                  {(revisions.data?.items ?? []).map((item) => (
                    <li key={item.revision_id} className="small">
                      第 {item.revision} 版 · {formatDate(item.created_at)} · {item.publication_status === "published" ? "已发布" : "未发布"} · {item.reason || "无理由"}
                    </li>
                  ))}
                </ul>
              </div>
              <p className="xs muted">已完成或进行中的活动锁定，不能删除或移动；历史版本只读。</p>
              <p className="xs muted"><IconArrowRight size={14} /> 关闭时若有未保存修改会先确认。</p>
            </aside>
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={confirmClose}
        title="放弃未保存的修改？"
        confirmLabel="放弃并关闭"
        cancelLabel="继续编辑"
        onConfirm={() => { setConfirmClose(false); setDirty(false); onClose(); }}
        onCancel={() => setConfirmClose(false)}
      >
        <p>当前草稿或未确认的预览会丢失；已经发布的内容不受影响。</p>
      </ConfirmDialog>
    </>
  );
}

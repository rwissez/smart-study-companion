import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorRequestId, newIdempotencyKey, toErrorMessage } from "../../api/client";
import {
  completeActivity,
  confirmAssistedHint,
  getAttempt,
  getMaterial,
  requestHint,
  startActivity,
  submitAttempt,
} from "../../api/service";
import type {
  AttemptView,
  MaterialView,
  PlanActivity,
  RecommendationBundle,
  SubmitResponse,
} from "../../api/types";
import { formatScore, gradingStatusLabel, questionTypeLabel } from "../../lib/format";
import { clearDraft, readDraft, writeDraft, type DraftScope } from "../../lib/draftStore";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import { StatePanel } from "../feedback/StatePanel";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { IconArrowLeft, IconClock, IconLock } from "../icons";

interface Props {
  plan: RecommendationBundle;
  planVersion: string;
  activity: PlanActivity;
  onChanged: () => void;
  onBack: () => void;
}

const MATH_SYMBOLS = ["−", "×", "÷", "²", "√", "≤", "≥", "≠", "(", ")", "/"];

function optionLetter(option: string, index: number): string {
  const match = option.trim().match(/^([A-Ha-h])[.、)．]/);
  return match ? match[1].toUpperCase() : String.fromCharCode(65 + index);
}

/**
 * 三题型统一作答：题干与选项来自学生安全 MaterialView；提交后只显示真实评分，
 * 待评分/待复核不显示 0 分；提示逐级请求，获取帮助前先确认独立性后果。
 */
export function AttemptEditor({ plan, planVersion, activity, onChanged, onBack }: Props) {
  const { scopeKey } = useSession();
  const [phase, setPhase] = useState<"starting" | "ready" | "completed" | "error">("starting");
  const [startError, setStartError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState<AttemptView | null>(null);
  const [choice, setChoice] = useState("");
  const [finalAnswer, setFinalAnswer] = useState("");
  const [steps, setSteps] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitResponse | null>(null);
  const [submitError, setSubmitError] = useState<{ text: string; requestId: string | null } | null>(null);
  const [hints, setHints] = useState<Record<number, string>>({});
  const [releasedLevel, setReleasedLevel] = useState(0);
  const [disclosure, setDisclosure] = useState<{ id: string; level: number; message: string } | null>(null);
  const [hintBusy, setHintBusy] = useState(false);
  const [hintError, setHintError] = useState<string | null>(null);
  const [hintNotice, setHintNotice] = useState<string | null>(null);
  const [confirmUnanswered, setConfirmUnanswered] = useState(false);
  const [materialBusy, setMaterialBusy] = useState(false);
  const [materialDone, setMaterialDone] = useState(activity.state === "completed");
  const submitKey = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const draftScope = useRef<DraftScope | null>(null);

  const materialId = activity.material_id ?? "";
  const material = useQuery<MaterialView>({
    queryKey: queryKeys.material(scopeKey, materialId, plan.catalog_version, planVersion, activity.activity_id),
    queryFn: () => getMaterial(materialId, {
      catalogVersion: plan.catalog_version,
      activityId: activity.activity_id,
      planVersion,
    }),
    enabled: Boolean(materialId) && activity.activity_kind !== "micro_lesson",
  });

  useEffect(() => {
    if (activity.activity_kind === "micro_lesson") {
      setPhase("ready");
      return;
    }
    let cancelled = false;
    setPhase("starting");
    startActivity(planVersion, activity.activity_id)
      .then((response) => {
        if (cancelled) return;
        if (response.state === "completed" && !response.attempt) {
          setPhase("completed");
          return;
        }
        if (response.attempt) {
          setAttempt(response.attempt);
          setReleasedLevel(response.attempt.hint_max_level ?? 0);
          const attemptId = response.attempt.attempt_id;
          const scope: DraftScope = {
            mode: scopeKey.mode,
            epoch: scopeKey.epoch,
            datasetId: plan.dataset_id,
            studentId: plan.student_id,
            planVersion,
            activityId: activity.activity_id,
            attemptId,
          };
          draftScope.current = scope;
          const draft = readDraft(scope);
          if (draft) {
            setChoice(draft.choice);
            setFinalAnswer(draft.finalAnswer);
            setSteps(draft.solutionSteps);
          }
          // 刷新后回显已释放提示；服务端只在 GET /attempts/{id} 返回 released_hints。
          void getAttempt(attemptId)
            .then((detail) => {
              if (cancelled) return;
              const released = detail.released_hints ?? [];
              if (released.length) {
                setHints(Object.fromEntries(released.map((item) => [item.level, item.text])));
                setReleasedLevel(Math.max(...released.map((item) => item.level)));
              }
            })
            .catch(() => {
              // 提示回显失败不影响作答本身。
            });
        }
        setPhase("ready");
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setStartError(reason);
        setPhase("error");
      });
    return () => {
      cancelled = true;
    };
  }, [activity.activity_id, activity.activity_kind, plan.dataset_id, plan.student_id, planVersion, scopeKey.epoch, scopeKey.mode]);

  useEffect(() => {
    if (!draftScope.current) return;
    writeDraft(draftScope.current, { choice, finalAnswer, solutionSteps: steps });
  }, [choice, finalAnswer, steps]);

  const questionType = material.data?.question_type ?? null;
  const locked = Boolean(result) || attempt?.status === "submitted" || attempt?.status === "completed";

  const insertSymbol = (symbol: string) => {
    const node = textareaRef.current;
    if (!node) {
      setSteps((previous) => previous + symbol);
      return;
    }
    const start = node.selectionStart ?? steps.length;
    const end = node.selectionEnd ?? steps.length;
    const next = `${steps.slice(0, start)}${symbol}${steps.slice(end)}`;
    setSteps(next);
    window.requestAnimationFrame(() => {
      node.focus();
      node.setSelectionRange(start + symbol.length, start + symbol.length);
    });
  };

  const doSubmit = async (unanswered: boolean) => {
    if (!attempt) return;
    if (!unanswered) {
      if (questionType === "choice" && !choice) {
        setValidationError("请选择一个选项后再提交。");
        return;
      }
      if (questionType === "fill_blank" && !finalAnswer.trim()) {
        setValidationError("请填写答案后再提交；确实不会可以标记未作答。");
        return;
      }
      if (questionType === "solution" && !finalAnswer.trim() && !steps.trim()) {
        setValidationError("请填写最终答案或解题过程后再提交；确实不会可以标记未作答。");
        return;
      }
      if (questionType !== "choice" && questionType !== "fill_blank" && questionType !== "solution") {
        setValidationError("这道题的题型没有标注，暂时不能提交。");
        return;
      }
    }
    setValidationError(null);
    setSubmitting(true);
    setSubmitError(null);
    submitKey.current = submitKey.current ?? newIdempotencyKey("attempt-submit");
    try {
      const response = await submitAttempt(
        attempt.attempt_id,
        unanswered
          ? { unanswered: true }
          : {
              final_answer: questionType === "choice" ? choice : finalAnswer.trim() || null,
              solution_steps: questionType === "solution" ? steps.trim() || null : null,
              unanswered: false,
            },
        submitKey.current,
      );
      setResult(response);
      setAttempt(response.attempt);
      setConfirmUnanswered(false);
      if (draftScope.current) clearDraft(draftScope.current);
      onChanged();
    } catch (reason) {
      setSubmitError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
      setConfirmUnanswered(false);
    } finally {
      setSubmitting(false);
    }
  };

  const applyHint = (level: number, text: string) => {
    setHints((previous) => ({ ...previous, [level]: text }));
    setReleasedLevel((previous) => Math.max(previous, level));
    setAttempt((previous) =>
      previous
        ? {
            ...previous,
            hint_max_level: Math.max(previous.hint_max_level ?? 0, level),
            assistance_status: "assisted",
          }
        : previous,
    );
    onChanged();
  };

  const requestNextHint = async (level: number) => {
    if (!attempt) return;
    setHintBusy(true);
    setHintError(null);
    setHintNotice(null);
    try {
      // 先按普通请求发送；独立模式会返回 requires_confirmation，而不是错误。
      const response = await requestHint(
        attempt.attempt_id,
        { level, allowAssisted: false },
        newIdempotencyKey(`hint-${level}`),
      );
      if (response.requires_confirmation && response.disclosure_id) {
        setDisclosure({
          id: response.disclosure_id,
          level,
          message: response.message ?? "这道题要求先独立完成。使用提示会把本次作答记为受助，需要你确认。",
        });
        return;
      }
      if (response.hint) {
        applyHint(level, response.hint);
        if (!attempt.hint_max_level) {
          setHintNotice("提示已释放；本次作答会记为受助，不再作为独立完成的证据。");
        }
      } else {
        setHintError("服务端没有返回提示内容。");
      }
    } catch (reason) {
      setHintError(toErrorMessage(reason));
    } finally {
      setHintBusy(false);
    }
  };

  const decideDisclosure = async (allow: boolean) => {
    if (!attempt || !disclosure) return;
    setHintBusy(true);
    setHintError(null);
    try {
      const response = await confirmAssistedHint(attempt.attempt_id, {
        disclosureId: disclosure.id,
        allow,
      });
      if (allow && response.released && response.hint) {
        applyHint(disclosure.level, response.hint);
        setHintNotice("已接受帮助，提示已释放；本次作答会记为受助。");
      } else if (allow) {
        setHintError("已接受帮助，但服务端没有返回提示内容。");
      } else {
        setHintNotice("你选择保持独立完成，本次没有释放提示。");
      }
      setDisclosure(null);
    } catch (reason) {
      setHintError(toErrorMessage(reason));
      setDisclosure(null);
    } finally {
      setHintBusy(false);
    }
  };

  const completeMaterial = async () => {
    setMaterialBusy(true);
    setSubmitError(null);
    try {
      await completeActivity(planVersion, activity.activity_id);
      setMaterialDone(true);
      onChanged();
    } catch (reason) {
      setSubmitError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setMaterialBusy(false);
    }
  };

  const back = (
    <button type="button" className="btn btnSm" onClick={onBack}>
      <IconArrowLeft size={16} />
      返回学习计划
    </button>
  );

  if (phase === "error") {
    return (
      <div className="stack">
        {back}
        <StatePanel
          kind="error"
          title="无法开始这个活动"
          message={toErrorMessage(startError)}
          requestId={errorRequestId(startError)}
        />
      </div>
    );
  }

  if (phase === "completed") {
    return (
      <div className="stack">
        {back}
        <StatePanel
          kind="history"
          title="该活动已完成"
          message="这里展示的是历史状态。评分或作答记录请通过证据入口查看；已经完成的活动不能重新开始。"
        />
      </div>
    );
  }

  if (phase === "starting" || material.isPending) {
    return <StatePanel kind="loading" title="正在准备活动内容" />;
  }

  if (material.isError) {
    return (
      <div className="stack">
        {back}
        <StatePanel
          kind="error"
          title="材料读取失败"
          message={toErrorMessage(material.error)}
          requestId={errorRequestId(material.error)}
        />
      </div>
    );
  }

  const materialData = material.data;

  if (activity.activity_kind === "study_material") {
    return (
      <div className="stack">
        {back}
        <div className="pageHeader">
          <div>
            <h1 className="pageTitle">{activity.title || "学习材料"}</h1>
            <p className="pageSub">{materialData?.source_label ?? "来源未标注"}</p>
          </div>
          <span className={`badge ${materialDone ? "badgeSuccess" : "badgeNeutral"}`}>{materialDone ? "已完成" : "进行中"}</span>
        </div>
        <div className="reading prose" style={{ whiteSpace: "pre-wrap" }}>{materialData?.content}</div>
        {materialData?.source_locator ? (
          <p className="xs muted">出处：{materialData.source_locator.source_file} · {materialData.source_locator.verification_status}</p>
        ) : null}
        <div className="submitBar">
          <button type="button" className="btn btnPrimary" onClick={() => void completeMaterial()} disabled={materialBusy || materialDone}>
            {materialDone ? "已完成" : materialBusy ? "提交中…" : "完成学习材料"}
          </button>
          <span className="small muted">完成只记录学习事件，不产生分数，也不推断掌握。</span>
        </div>
        {submitError ? <p className="fieldError" role="alert">{submitError.text}</p> : null}
      </div>
    );
  }

  const hintsTotal = activity.hint_levels.length;
  const grade = result?.grade ?? null;
  const graded = Boolean(grade && grade.status === "graded" && grade.validation_status === "validated" && grade.score !== null);

  return (
    <div className="stack">
      <div className="rowBetween">
        {back}
        <span className="small muted">提交后保留原卷，评分以服务端为准</span>
      </div>
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">在线作答</h1>
          <p className="pageSub">
            {questionTypeLabel(questionType)} · 满分 {materialData?.max_score ?? "—"} · 预计 {activity.estimate_minutes} 分钟
            {locked ? " · 已提交" : ""}
          </p>
        </div>
        <div className="tagRow">
          <span className="tag">{activity.purpose || "按计划练习"}</span>
          {attempt?.assistance_status === "assisted" ? <span className="tag tagWarning">本次作答已获取帮助</span> : null}
        </div>
      </div>

      <div className="attemptLayout">
        <div className="stack">
          <div className="questionBody">{materialData?.content}</div>

          {questionType === "choice" ? (
            <div className="optionList" role="radiogroup" aria-label="选项">
              {(materialData?.options ?? []).map((option, index) => {
                const letter = optionLetter(option, index);
                const selected = choice === option;
                return (
                  <label key={option} className={selected ? "optionItem optionItemSelected" : "optionItem"}>
                    <input
                      type="radio"
                      name={`choice-${activity.activity_id}`}
                      value={option}
                      checked={selected}
                      disabled={locked}
                      onChange={() => setChoice(option)}
                    />
                    <span>{option}</span>
                    <span className="srOnly">选项 {letter}</span>
                  </label>
                );
              })}
            </div>
          ) : null}

          {questionType === "fill_blank" ? (
            <label className="field" style={{ marginTop: 16 }}>
              <span className="fieldLabel">填写答案</span>
              <input
                className="input"
                value={finalAnswer}
                disabled={locked}
                onChange={(event) => setFinalAnswer(event.target.value)}
                placeholder="可以直接填数值或表达式"
              />
              <span className="fieldHint">按原题要求填写；不需要写解题过程。</span>
            </label>
          ) : null}

          {questionType === "solution" ? (
            <div className="stack" style={{ marginTop: 16 }}>
              <div className="mathToolbar" aria-label="数学符号插入">
                {MATH_SYMBOLS.map((symbol) => (
                  <button
                    key={symbol}
                    type="button"
                    className="mathKey"
                    disabled={locked}
                    onClick={() => insertSymbol(symbol)}
                  >
                    {symbol}
                  </button>
                ))}
              </div>
              <label className="field">
                <span className="fieldLabel">解题过程</span>
                <textarea
                  ref={textareaRef}
                  className="textarea"
                  style={{ minHeight: 200 }}
                  value={steps}
                  disabled={locked}
                  onChange={(event) => setSteps(event.target.value)}
                  placeholder="按自己的步骤写，换行保留原样"
                />
              </label>
              <label className="field">
                <span className="fieldLabel">最终答案</span>
                <input
                  className="input"
                  value={finalAnswer}
                  disabled={locked}
                  onChange={(event) => setFinalAnswer(event.target.value)}
                  placeholder="例如 x=5"
                />
                <span className="fieldHint">允许只写过程或只写答案，提交后按服务端评分点判断。</span>
              </label>
            </div>
          ) : null}

          {questionType && !["choice", "fill_blank", "solution"].includes(questionType) ? (
            <StatePanel kind="not-ready" title="题型未接入" message={`服务返回的题型是 ${questionType}，当前组件只支持选择、填空与解答三类。`} />
          ) : null}

          {validationError ? <p className="fieldError" role="alert">{validationError}</p> : null}
          {submitError ? (
            <p className="fieldError" role="alert">
              {submitError.text}
              {submitError.requestId ? `（请求编号 ${submitError.requestId}）` : ""}
              <span className="xs muted"> 原作答没有改变，可以用同一请求编号重试。</span>
            </p>
          ) : null}

          <div className="submitBar">
            <button
              type="button"
              className="btn btnPrimary"
              onClick={() => void doSubmit(false)}
              disabled={submitting || locked || !attempt}
            >
              {locked ? "已提交" : submitting ? "提交中…" : "提交作答"}
            </button>
            {!locked ? (
              <button type="button" className="btn" onClick={() => setConfirmUnanswered(true)} disabled={submitting}>
                标记未作答并交卷
              </button>
            ) : null}
            {!locked ? <span className="small muted">提交后答卷锁定，不能修改。</span> : null}
          </div>

          {locked ? (
            <div className="stack">
              <div className="gradeBox">
                <div className="rowBetween">
                  <span className="small muted">评分结果</span>
                  <span className={`badge ${graded ? "badgeSuccess" : "badgeWarning"}`}>
                    {graded ? "已评分" : gradingStatusLabel(grade?.status ?? result?.feedback_status ?? "pending")}
                  </span>
                </div>
                {graded && grade ? (
                  <>
                    <div className="gradeScore tabular">{formatScore(grade.score, grade.max_score)}</div>
                    <p className="small muted">评分方式：{grade.method === "model" ? "模型评分（已校验）" : "规则评分"}</p>
                    {grade.rubric_scores.length ? (
                      <ul className="small">
                        {grade.rubric_scores.map((item) => (
                          <li key={item.point_id}>
                            {item.description ?? item.point_id}：{formatScore(item.score, item.max_score)}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </>
                ) : (
                  <>
                    <div className="gradeScore" style={{ fontSize: "var(--font-section)" }}>已提交，待评分/待复核</div>
                    <p className="small muted">
                      {grade?.limitations.length ? grade.limitations.join("；") : "服务端还没有给出可用分数。待评分不是 0 分。"}
                    </p>
                  </>
                )}
                {result?.group_status?.group_completed ? (
                  <p className="small">任务组已完成，后续安排以服务端最新方案为准。</p>
                ) : null}
              </div>

              <div>
                <h3 className="paneTitle">已提交的作答</h3>
                <p className="answerLocked prose">{questionType === "choice" ? choice : finalAnswer || "（未填写最终答案）"}</p>
                {questionType === "solution" && steps ? <p className="answerLocked prose small muted">{steps}</p> : null}
              </div>
            </div>
          ) : null}
        </div>

        <aside className="stack" aria-label="提示与帮助">
          <div className="hintBox">
            <div className="rowBetween">
              <h3 className="paneTitle" style={{ marginBottom: 0 }}>分级提示</h3>
              <span className="xs muted">{releasedLevel} / {hintsTotal} 级已释放</span>
            </div>
            <p className="xs muted" style={{ marginTop: 4 }}>
              提示逐级释放；获取帮助后，本次作答不再作为独立完成的证据。
            </p>
            {Object.entries(hints)
              .map(([level, text]) => Number(level) <= hintsTotal ? { level: Number(level), text } : null)
              .filter((item): item is { level: number; text: string } => Boolean(item))
              .sort((a, b) => a.level - b.level)
              .map((item) => (
                <div className="hintItem" key={item.level}>
                  <div className="hintLevel">提示 {item.level}</div>
                  <p className="small">{item.text}</p>
                </div>
              ))}
            {releasedLevel > 0 && Object.keys(hints).length === 0 ? (
              <p className="small muted">本次作答已释放 {releasedLevel} 级提示；本页没有缓存提示文本。</p>
            ) : null}
            {!locked && releasedLevel < hintsTotal ? (
              <button
                type="button"
                className="btn btnSm"
                style={{ marginTop: 8 }}
                disabled={hintBusy}
                onClick={() => void requestNextHint(releasedLevel + 1)}
              >
                <IconClock size={16} />
                {hintBusy ? "请求中…" : `获取第 ${releasedLevel + 1} 级提示`}
              </button>
            ) : locked ? (
              <p className="small muted row" style={{ marginTop: 8 }}><IconLock size={14} /> 已提交，不能再获取提示。</p>
            ) : (
              <p className="small muted" style={{ marginTop: 8 }}>提示已全部释放。</p>
            )}
            {hintNotice ? <p className="small" role="status">{hintNotice}</p> : null}
            {hintError ? <p className="fieldError" role="alert">{hintError}</p> : null}
          </div>
          <div className="small muted">
            独立性状态：{attempt?.assistance_status === "assisted" ? "已获取帮助（受助）" : attempt?.assistance_status === "unknown" ? "帮助情况未知" : "目前未使用帮助"}
          </div>
          <p className="xs muted">学生端不会收到标准答案、评分细则或未释放提示；服务端已裁剪相关字段。</p>
        </aside>
      </div>

      <ConfirmDialog
        open={disclosure !== null}
        title="使用提示需要你确认"
        confirmLabel="接受帮助并查看提示"
        cancelLabel="保持独立完成"
        onConfirm={() => void decideDisclosure(true)}
        onCancel={() => void decideDisclosure(false)}
        busy={hintBusy}
      >
        <p>{disclosure?.message ?? "使用提示会把本次作答记为受助，需要你确认。"}</p>
        <p className="small muted">
          选择“保持独立完成”不会释放提示，也不会把本次作答记为受助；服务端会记录你选择不接收帮助。
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={confirmUnanswered}
        title="确认标记未作答"
        confirmLabel="确认交卷"
        onConfirm={() => void doSubmit(true)}
        onCancel={() => setConfirmUnanswered(false)}
        busy={submitting}
      >
        <p>标记未作答后本次作答会结束，不产生有效分，也不按 0 分处理。确定交卷吗？</p>
      </ConfirmDialog>
    </div>
  );
}

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { decidePlanReview, listPlanReviews, listRuns } from "../../api/service";
import type { PlanReviewDecisionResult, PlanReviewItem, RevisionWarning } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { formatDate } from "../../lib/format";
import { StatePanel } from "../../components/feedback/StatePanel";
import { Modal } from "../../components/ui/Modal";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconArrowRight } from "../../components/icons";

function checkSummary(item: PlanReviewItem): string[] {
  const checks = Array.isArray((item.review_info as Record<string, unknown>).checks)
    ? ((item.review_info as Record<string, unknown>).checks as Array<Record<string, unknown>>)
    : [];
  return checks
    .filter((check) => check.passed === false)
    .map((check) => String(check.reason ?? "检查未通过"));
}

function warningCount(item: PlanReviewItem): number {
  return Array.isArray(item.warnings) ? item.warnings.length : 0;
}

export function PlanReviewPage() {
  const { scopeKey } = useSession();
  const datasetId = scopeKey.datasetId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [decision, setDecision] = useState<{ item: PlanReviewItem; action: "approve" | "reject" } | null>(null);
  const [editItem, setEditItem] = useState<PlanReviewItem | null>(null);
  const [reason, setReason] = useState("");
  const [goal, setGoal] = useState("");
  const [intro, setIntro] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; text: string; requestId: string | null; details: Array<{ field?: string; reason?: string }> } | null>(null);
  const [result, setResult] = useState<PlanReviewDecisionResult | null>(null);

  const reviews = useQuery({
    queryKey: queryKeys.planReviews(scopeKey, page),
    queryFn: () => listPlanReviews(datasetId as string, { page, pageSize: 20 }),
    enabled: Boolean(datasetId),
  });

  const runs = useQuery({
    queryKey: queryKeys.runs(scopeKey),
    queryFn: () => listRuns(datasetId as string),
    enabled: Boolean(datasetId),
  });
  const nodeId = runs.data?.find((item) => item.status === "succeeded")?.node_ids[0]
    ?? runs.data?.[0]?.node_ids[0]
    ?? "N01";

  const resetDialog = () => {
    setDecision(null);
    setEditItem(null);
    setReason("");
    setGoal("");
    setIntro("");
    setError(null);
  };

  const submitDecision = async () => {
    if (!decision) return;
    if (decision.action === "reject" && !reason.trim()) {
      setError({ code: "VALIDATION_ERROR", text: "驳回需要填写理由。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await decidePlanReview(datasetId as string, decision.item.review_id, nodeId, {
        decision: decision.action,
        reason: reason.trim(),
        basePlanVersion: decision.item.plan_version,
      });
      setResult(response);
      await queryClient.invalidateQueries({ queryKey: queryKeys.planReviews(scopeKey, page) });
      await reviews.refetch();
      resetDialog();
    } catch (reasonError) {
      const code = errorCode(reasonError);
      setError({
        code,
        text:
          code === "CONFLICT" || code === "REVISION_CONFLICT"
            ? "该候选已被处理或已过期，已为你刷新列表。"
            : toErrorMessage(reasonError),
        requestId: errorRequestId(reasonError),
        details: (reasonError as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
      });
      if (code === "CONFLICT" || code === "REVISION_CONFLICT") {
        await reviews.refetch();
      }
    } finally {
      setBusy(false);
    }
  };

  const submitEdit = async () => {
    if (!editItem) return;
    if (!reason.trim()) {
      setError({ code: "VALIDATION_ERROR", text: "修改需要填写理由。", requestId: null, details: [] });
      return;
    }
    if (!goal.trim() && !intro.trim()) {
      setError({ code: "VALIDATION_ERROR", text: "至少填写一项修改内容（学习目标或学生开场白）。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await decidePlanReview(datasetId as string, editItem.review_id, nodeId, {
        decision: "edit",
        reason: reason.trim(),
        basePlanVersion: editItem.plan_version,
        changes: {
          ...(goal.trim() ? { goal: goal.trim() } : {}),
          ...(intro.trim() ? { student_intro: intro.trim() } : {}),
        },
      });
      setResult(response);
      await queryClient.invalidateQueries({ queryKey: queryKeys.planReviews(scopeKey, page) });
      await reviews.refetch();
      resetDialog();
    } catch (reasonError) {
      setError({
        code: errorCode(reasonError),
        text: toErrorMessage(reasonError),
        requestId: errorRequestId(reasonError),
        details: (reasonError as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
      });
    } finally {
      setBusy(false);
    }
  };

  if (!datasetId) {
    return <StatePanel kind="empty" title="当前会话没有绑定数据集" message="选择数据集后才能查看方案审核队列。" />;
  }

  const total = reviews.data?.total ?? 0;
  const pageSize = reviews.data?.page_size ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">方案审核</h1>
          <p className="pageSub">教师对话生成的候选方案在这里审核；通过后才发布为正式方案。</p>
        </div>
        <div className="pageActions">
          <button type="button" className="btn" onClick={() => void reviews.refetch()}>刷新</button>
        </div>
      </div>

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
            {result.preview_id ? <><dt>预览</dt><dd>已生成新的候选预览（未发布）</dd></> : null}
            {result.plan_version ? <><dt>新方案版本</dt><dd>{result.plan_version}</dd></> : null}
            {result.publication_status ? (
              <>
                <dt>发布状态</dt>
                <dd>{result.publication_status === "published" ? "已发布" : result.publication_status}</dd>
              </>
            ) : null}
            <dt>审核状态</dt>
            <dd>
              {result.review_status === "validated" || result.review_status === "approved" || result.review_status === "human_confirmed"
                ? "已通过"
                : result.review_status === "rejected"
                  ? "已驳回"
                  : result.review_status}
            </dd>
          </dl>
          {result.warnings?.length ? (
            <ul>{result.warnings.map((warning: RevisionWarning, index: number) => <li key={index} className="small">{warning.message}</li>)}</ul>
          ) : null}
          {result.preview_id ? (
            <span className="small muted">修改后预览不会直接发布；请到学生画像的方案编辑器继续确认发布。</span>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <div className="statePanel stateError" role="alert">
          <span className="stateTitle">审核未完成（{error.code}）</span>
          <span>{error.text}</span>
          {error.requestId ? <span className="xs">请求编号：{error.requestId}</span> : null}
          {error.details.length ? (
            <ul>{error.details.map((detail, index) => <li key={index} className="small">{detail.field ? `${detail.field}：` : ""}{detail.reason}</li>)}</ul>
          ) : null}
        </div>
      ) : null}

      {reviews.isPending ? <StatePanel kind="loading" title="正在读取方案审核队列" /> : null}
      {reviews.isError ? (
        <StatePanel
          kind="error"
          title="方案审核队列读取失败"
          message={toErrorMessage(reviews.error)}
          requestId={errorRequestId(reviews.error)}
          onRetry={() => void reviews.refetch()}
        />
      ) : null}

      {reviews.data && total === 0 ? (
        <StatePanel
          kind="empty"
          title="当前没有待审核的方案候选"
          message="候选来自教师对话中的生成操作：从学生画像进入 AI 对话，点击“生成方案候选”但不确认，候选就会出现在这里。"
          action={<button type="button" className="btn" onClick={() => navigate("/teacher/classes")}>去班级总览</button>}
        />
      ) : null}

      {reviews.data && total > 0 ? (
        <>
          <div className="tableWrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">学生</th>
                  <th scope="col">基础方案版本</th>
                  <th scope="col">候选摘要</th>
                  <th scope="col" className="numeric">预计用时</th>
                  <th scope="col" className="numeric">警告</th>
                  <th scope="col">过期时间</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {reviews.data.items.map((item) => {
                  const failedChecks = checkSummary(item);
                  return (
                    <tr key={item.review_id}>
                      <td>{item.student_id}</td>
                      <td className="small muted">{item.plan_version || "—"}</td>
                      <td className="small">
                        {item.summary || "按当前学情重新生成的候选安排"}
                        {failedChecks.length ? <div className="xs fieldError">检查未通过：{failedChecks.join("；")}</div> : null}
                        {item.warnings?.length ? (
                          <details>
                            <summary className="xs muted">查看警告</summary>
                            <ul>{item.warnings.map((warning, index) => <li key={index} className="xs">{warning.message}</li>)}</ul>
                          </details>
                        ) : null}
                      </td>
                      <td className="numeric">{item.estimated_minutes ?? "—"} 分钟</td>
                      <td className="numeric">{warningCount(item)}</td>
                      <td className="small">{formatDate(item.expires_at)}</td>
                      <td>
                        <div className="row">
                          <button
                            type="button"
                            className="btn btnSm btnPrimary"
                            onClick={() => { setResult(null); setError(null); setReason(""); setDecision({ item, action: "approve" }); }}
                          >
                            通过
                          </button>
                          <button
                            type="button"
                            className="btn btnSm"
                            onClick={() => { setResult(null); setError(null); setReason(""); setDecision({ item, action: "reject" }); }}
                          >
                            驳回
                          </button>
                          <button
                            type="button"
                            className="btn btnSm"
                            onClick={() => { setResult(null); setError(null); setReason(""); setGoal(""); setIntro(""); setEditItem(item); }}
                          >
                            修改后预览
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <span className="small muted">共 {total} 条 · 第 {page} / {totalPages} 页</span>
            <button type="button" className="btn btnSm" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
            <button type="button" className="btn btnSm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
          </div>
        </>
      ) : null}

      <ConfirmDialog
        open={decision !== null}
        title={decision?.action === "approve" ? "确认通过并发布候选方案" : "确认驳回候选方案"}
        confirmLabel={decision?.action === "approve" ? "确认通过" : "确认驳回"}
        onConfirm={() => void submitDecision()}
        onCancel={resetDialog}
        busy={busy}
        danger={decision?.action === "reject"}
      >
        <p>
          {decision?.action === "approve"
            ? `学生会读到新方案，原方案保留历史。候选来自 ${decision?.item.student_id} 的正式学情，通过后由服务端发布。`
            : `驳回只记录审核决定，不改变学生当前方案。`}
        </p>
        <label className="field">
          <span className="fieldLabel">审核理由{decision?.action === "reject" ? "（必填）" : "（可选）"}</span>
          <textarea
            className="textarea"
            style={{ minHeight: 80 }}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
            placeholder={decision?.action === "approve" ? "例如：与当前学情一致" : "例如：材料选择不合理"}
          />
        </label>
        {error ? <p className="fieldError" role="alert">{error.text}</p> : null}
      </ConfirmDialog>

      <Modal
        open={editItem !== null}
        title="修改候选并生成预览"
        description={editItem ? `学生 ${editItem.student_id} · 修改后只生成预览，不直接发布` : ""}
        onClose={resetDialog}
        footer={
          <>
            <button type="button" className="btn" onClick={resetDialog} disabled={busy}>取消</button>
            <button type="button" className="btn btnPrimary" onClick={() => void submitEdit()} disabled={busy}>
              {busy ? "提交中…" : "生成修改预览"}
            </button>
          </>
        }
      >
        <div className="stack">
          <label className="field">
            <span className="fieldLabel">学习目标</span>
            <textarea className="textarea" style={{ minHeight: 80 }} value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="留空表示不修改" />
          </label>
          <label className="field">
            <span className="fieldLabel">学生开场白</span>
            <textarea className="textarea" style={{ minHeight: 80 }} value={intro} onChange={(event) => setIntro(event.target.value)} placeholder="留空表示不修改" />
          </label>
          <label className="field">
            <span className="fieldLabel">修改理由（必填）</span>
            <textarea className="textarea" style={{ minHeight: 80 }} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </label>
          {error ? (
            <div className="fieldError" role="alert">
              {error.text}
              {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
            </div>
          ) : null}
          <p className="xs muted">修改结果是新的候选预览；需要教师再次审核或到学生画像的方案编辑器确认发布。</p>
        </div>
      </Modal>

      {reviews.data && total === 0 ? null : (
        <p className="xs muted row"><IconArrowRight size={14} /> 审核动作会写入审核记录；未通过的候选不会影响学生当前正式方案。</p>
      )}
    </div>
  );
}

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorCode, errorRequestId, newIdempotencyKey, toErrorMessage } from "../../api/client";
import { getIntervention, listRuns, resolveIntervention } from "../../api/service";
import type { EvidenceRef, InterventionView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { formatDate } from "../../lib/format";
import { nodeLabel } from "../../lib/labels";
import { StatePanel } from "../../components/feedback/StatePanel";
import { EvidenceDrawer } from "../../components/evidence/EvidenceDrawer";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconArrowLeft } from "../../components/icons";

const REASON_LABELS: Record<string, string> = {
  material_shortage: "可用材料不足",
  insufficient_evidence: "证据不足",
  strategy_blocked: "策略无法推进",
  repeated_failure: "连续未完成",
  teacher_attention: "需要教师关注",
};

export function InterventionPage() {
  const { caseId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { scopeKey } = useSession();
  const datasetId = scopeKey.datasetId;
  const studentId = searchParams.get("studentId") ?? "";
  const nodeParam = searchParams.get("nodeId") ?? "";
  const runParam = searchParams.get("runId") ?? "";

  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);
  const [confirmResolve, setConfirmResolve] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; requestId: string | null } | null>(null);

  const runs = useQuery({
    queryKey: queryKeys.runs(scopeKey),
    queryFn: () => listRuns(datasetId as string),
    enabled: Boolean(datasetId) && !runParam,
  });
  const run = runParam || runs.data?.find((item) => item.status === "succeeded")?.run_id || runs.data?.[0]?.run_id || "";
  const nodes = runs.data?.find((item) => item.run_id === run)?.node_ids ?? [];
  const nodeId = nodeParam || nodes[0] || "N01";

  const detail = useQuery<InterventionView>({
    queryKey: queryKeys.intervention(scopeKey, run, studentId, caseId, nodeId),
    queryFn: () => getIntervention(run, studentId, caseId, nodeId),
    enabled: Boolean(datasetId && run && studentId && caseId && nodeId),
  });

  const submit = async () => {
    if (!reason.trim()) {
      setError({ text: "结案理由不能为空。", requestId: null });
      return;
    }
    if (!detail.data) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await resolveIntervention(
        run,
        studentId,
        caseId,
        { nodeId, expectedLastCutoff: detail.data.last_cutoff, reason: reason.trim() },
        newIdempotencyKey("intervention-resolve"),
      );
      setConfirmResolve(false);
      setReason("");
      setNotice("已结案。已读不等于结案；结案记录会写入处理轨迹。");
      await detail.refetch();
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts(scopeKey) });
    } catch (reasonError) {
      const code = errorCode(reasonError);
      if (code === "REVISION_CONFLICT" || code === "CONFLICT") {
        setNotice("该案例已被其他人处理或证据水位已更新，已为你刷新。");
        setConfirmResolve(false);
        await detail.refetch();
      } else {
        setError({ text: toErrorMessage(reasonError), requestId: errorRequestId(reasonError) });
      }
    } finally {
      setBusy(false);
    }
  };

  if (!studentId) {
    return (
      <StatePanel
        kind="empty"
        title="缺少学生定位"
        message="介入详情需要学生编号与节点；请从顶部提醒或学生画像进入。"
        action={<button type="button" className="btn" onClick={() => navigate("/teacher/classes")}>返回班级总览</button>}
      />
    );
  }

  if (detail.isPending) return <StatePanel kind="loading" title="正在读取介入案例" />;
  if (detail.isError) {
    return (
      <div className="stack">
        <div className="row">
          <button type="button" className="btn btnSm" onClick={() => navigate(`/teacher/students/${encodeURIComponent(studentId)}`)}>
            <IconArrowLeft size={16} /> 返回学生画像
          </button>
        </div>
        <StatePanel
          kind={errorCode(detail.error) === "NOT_FOUND" ? "empty" : "error"}
          title={errorCode(detail.error) === "NOT_FOUND" ? "介入案例不存在" : "介入案例读取失败"}
          message={
            errorCode(detail.error) === "NOT_FOUND"
              ? "当前数据里没有这个案例。介入案例由系统在材料不足、证据不足或反复失败时生成。"
              : toErrorMessage(detail.error)
          }
          requestId={errorRequestId(detail.error)}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }

  const data = detail.data;
  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <div className="row" style={{ gap: 12 }}>
            <button type="button" className="btn btnSm" onClick={() => navigate(`/teacher/students/${encodeURIComponent(studentId)}`)}>
              <IconArrowLeft size={16} /> 返回学生画像
            </button>
            <h1 className="pageTitle">介入案例 · {studentId}</h1>
          </div>
          <p className="pageSub">{nodeLabel(nodeId, nodes)} · {REASON_LABELS[data.reason_code] ?? "需要关注"} · {data.status === "open" ? "处理中" : "已结案"}</p>
        </div>
        <div className="pageActions">
          <span className={`badge ${data.status === "open" ? "badgeWarning" : "badgeSuccess"}`}>{data.status === "open" ? "待处理" : "已结案"}</span>
        </div>
      </div>

      <div className="grid2">
        <section className="stack">
          <div className="sectionHead">
            <h2 className="sectionTitle">案例说明</h2>
            <span className="sectionHint">打开于 {formatDate(data.opened_at)}</span>
          </div>
          <dl className="kv">
            <dt>学生</dt><dd>{studentId || data.student_id}</dd>
            <dt>原因</dt><dd>{REASON_LABELS[data.reason_code] ?? "需要教师关注"}</dd>
            <dt>未解决问题</dt><dd>{data.unresolved_question || "未填写"}</dd>
            <dt>建议动作</dt><dd>{data.suggested_actions.length ? data.suggested_actions.join("；") : "无"}</dd>
            <dt>观察到的效果</dt><dd>{data.observed_effects.length ? data.observed_effects.join("；") : "暂无"}</dd>
            <dt>证据水位</dt><dd className="tabular">{data.last_cutoff}</dd>
          </dl>
          <div>
            <h3 className="paneTitle">相关证据</h3>
            {data.evidence_refs.length === 0 ? (
              <p className="small muted">案例没有附带证据引用。</p>
            ) : (
              <div className="refList">
                {data.evidence_refs.map((ref, index) => (
                  <button key={`${ref.kind}-${index}`} type="button" className="btn btnSm" onClick={() => setEvidence(ref)}>
                    {ref.kind === "record" ? "作答记录" : ref.kind === "metric" ? "指标" : "证据"} · 第 {index + 1} 条
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="stack">
          <div className="sectionHead">
            <h2 className="sectionTitle">结案</h2>
            <span className="sectionHint">结案不改评分、学情与已发布方案</span>
          </div>
          {notice ? <p className="small" role="status">{notice}</p> : null}
          {data.status === "open" ? (
            <div className="stackSm">
              <label className="field">
                <span className="fieldLabel">结案理由（必填）</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 110 }}
                  value={reason}
                  maxLength={500}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="例如：已与学生当面沟通，补证材料已安排"
                />
              </label>
              <div className="row">
                <button type="button" className="btn btnPrimary" onClick={() => setConfirmResolve(true)} disabled={busy || !reason.trim()}>
                  确认结案
                </button>
                <span className="small muted">提交时会带当前证据水位，避免覆盖别人的处理。</span>
              </div>
              {error ? (
                <p className="fieldError" role="alert">{error.text}{error.requestId ? `（请求编号 ${error.requestId}）` : ""}</p>
              ) : null}
            </div>
          ) : (
            <p className="small muted">该案例已结案，不能重复结案。</p>
          )}

          <div>
            <h3 className="paneTitle">处理轨迹</h3>
            {data.handling_history.length === 0 ? (
              <p className="small muted">还没有处理记录。</p>
            ) : (
              <ul className="sourceList">
                {data.handling_history.map((entry, index) => (
                  <li key={index} className="small">
                    {formatDate(entry.handled_at)} · 结案 · {entry.reason || "未填写理由"}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={confirmResolve}
        title="确认结案"
        confirmLabel="确认结案"
        onConfirm={() => void submit()}
        onCancel={() => setConfirmResolve(false)}
        busy={busy}
      >
        <p>结案会记录处理轨迹并关闭提醒；不会修改学生成绩、学情或已发布方案。</p>
      </ConfirmDialog>

      <EvidenceDrawer
        refItem={evidence}
        context={{ student_id: studentId }}
        onClose={() => setEvidence(null)}
      />
    </div>
  );
}

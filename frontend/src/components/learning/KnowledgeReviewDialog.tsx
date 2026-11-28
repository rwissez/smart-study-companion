import { useState } from "react";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { decideKnowledgeReview } from "../../api/service";
import type { KnowledgeReviewItem } from "../../api/types";
import { Modal } from "../ui/Modal";

interface Props {
  open: boolean;
  datasetId: string;
  item: KnowledgeReviewItem | null;
  onClose: () => void;
  onDone: (message: string) => void;
}

type Decision = "approve" | "reject" | "edit";

/** 从 checks 里的 REVIEW_CONTEXT 找候选版本；列表接口自身未填 candidate_version。 */
export function reviewCandidateVersion(item: KnowledgeReviewItem): string {
  const checks = Array.isArray((item.review_info as Record<string, unknown>).checks)
    ? ((item.review_info as Record<string, unknown>).checks as Array<Record<string, unknown>>)
    : [];
  const context = checks.find((check) => typeof check.candidate_version === "string" && check.candidate_version);
  return context ? String(context.candidate_version) : item.candidate_version;
}

export function KnowledgeReviewDialog({ open, datasetId, item, onClose, onDone }: Props) {
  const [decision, setDecision] = useState<Decision>("approve");
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState("");
  const [definition, setDefinition] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; requestId: string | null; details: Array<{ field?: string; reason?: string }> } | null>(null);

  if (!item) return null;
  const candidateVersion = reviewCandidateVersion(item);
  const editable = item.object_type === "knowledge_point";

  const submit = async () => {
    if (!reason.trim()) {
      setError({ text: "请填写审核理由。", requestId: null, details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await decideKnowledgeReview(datasetId, item.review_id, {
        baseCandidateVersion: candidateVersion,
        decision,
        changes:
          decision === "edit"
            ? {
                ...(title.trim() ? { title: title.trim() } : {}),
                ...(definition.trim() ? { definition: definition.trim() } : {}),
              }
            : null,
        reason: reason.trim(),
      });
      onDone(
        decision === "approve"
          ? `已通过审核并发布新的知识版本${result.knowledge_version ? "" : "（服务端未返回版本号）"}。`
          : decision === "reject"
            ? "已拒绝该候选，正式知识版本不变。"
            : "编辑已保存为新的候选，仍需再次审核后才能发布。",
      );
      setReason("");
      setTitle("");
      setDefinition("");
      onClose();
    } catch (reasonError) {
      const code = errorCode(reasonError);
      if (code === "REVISION_CONFLICT" || code === "CONFLICT") {
        setError({ text: "候选已更新或已被处理，页面会刷新审核列表。", requestId: errorRequestId(reasonError), details: [] });
      } else {
        setError({
          text: toErrorMessage(reasonError),
          requestId: errorRequestId(reasonError),
          details: (reasonError as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title="审核知识候选"
      description={`${item.object_type === "knowledge_point" ? "知识点" : item.object_type === "relation" ? "关系" : "材料关联"} · ${item.summary || "待审核对象"}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
          <button type="button" className="btn btnPrimary" onClick={() => void submit()} disabled={busy || !candidateVersion}>
            {busy ? "提交中…" : "提交审核决定"}
          </button>
        </>
      }
    >
      <div className="stack">
        {!candidateVersion ? (
          <p className="fieldError" role="alert">
            审核列表没有返回候选版本号，服务端会拒绝这个决定；请刷新列表或等后端补齐 candidate_version。
          </p>
        ) : null}
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="fieldLabel">决定</legend>
          <div className="row">
            {(["approve", "reject", "edit"] as Decision[]).map((value) => (
              <label className="checkRow" key={value}>
                <input
                  type="radio"
                  name="knowledge-decision"
                  checked={decision === value}
                  disabled={value === "edit" && !editable}
                  onChange={() => setDecision(value)}
                />
                {value === "approve" ? "通过并发布" : value === "reject" ? "拒绝" : "编辑后重审"}
              </label>
            ))}
          </div>
        </fieldset>
        {decision === "edit" ? (
          editable ? (
            <div className="stackSm">
              <label className="field">
                <span className="fieldLabel">标题（可选）</span>
                <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
              </label>
              <label className="field">
                <span className="fieldLabel">定义（可选）</span>
                <textarea className="textarea" style={{ minHeight: 100 }} value={definition} onChange={(event) => setDefinition(event.target.value)} />
              </label>
            </div>
          ) : (
            <p className="small muted">该对象类型不支持在页面编辑，只能用通过或拒绝。</p>
          )
        ) : null}
        <label className="field">
          <span className="fieldLabel">审核理由</span>
          <textarea
            className="textarea"
            style={{ minHeight: 90 }}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
            placeholder="写明依据，例如：定义与课程要求一致"
          />
        </label>
        {error ? (
          <div className="fieldError" role="alert">
            {error.text}
            {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
            {error.details.length ? (
              <ul>{error.details.map((detail, index) => <li key={index} className="small">{detail.field ? `${detail.field}：` : ""}{detail.reason}</li>)}</ul>
            ) : null}
          </div>
        ) : null}
        <p className="xs muted">通过会生成新的不可变知识版本并切换正式指针；拒绝只记录决定；编辑生成新的候选，不直接发布。</p>
      </div>
    </Modal>
  );
}

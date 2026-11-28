import type { EvidenceRef, Finding } from "../../api/types";
import { evidenceStatusLabel, performanceLabel } from "../../lib/format";
import { EvidenceRefsButton } from "../evidence/EvidenceRefsButton";
import type { EvidenceContext } from "../evidence/types";

interface Props {
  findings: Finding[];
  names: Record<string, string>;
  onEvidence?: (ref: EvidenceRef) => void;
  emptyText?: string;
  evidenceContext?: EvidenceContext;
  materialNames?: Record<string, string>;
}

const PERFORMANCE_TONE: Record<string, string> = {
  observed_strength: "tagAccent",
  needs_practice: "tagWarning",
  undetermined: "tag",
};

function diagnosticText(value: Finding["diagnostic_details"]): string[] {
  if (!value) return [];
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (typeof item === "string") return item.trim() ? [item] : [];
      if (item && typeof item === "object" && "summary" in item) {
        const summary = String((item as { summary?: unknown }).summary ?? "").trim();
        return summary ? [summary] : [];
      }
      return [];
    });
  }
  return Object.entries(value).flatMap(([key, item]) => {
    if (typeof item === "string" && item.trim()) return [`${key}：${item}`];
    if (typeof item === "number") return [`${key}：${item}`];
    if (Array.isArray(item)) {
      const lines = item.filter((entry): entry is string => typeof entry === "string");
      return lines.length ? [`${key}：${lines.join("、")}`] : [];
    }
    return [];
  });
}

export function FindingsList({
  findings,
  names,
  onEvidence,
  emptyText = "本次分析没有返回诊断发现。",
  evidenceContext,
  materialNames,
}: Props) {
  if (findings.length === 0) {
    return <p className="muted small">{emptyText}</p>;
  }
  return (
    <div className="findingList">
      {findings.map((finding) => (
        <article className="findingItem" key={finding.finding_id}>
          <div className="findingHead">
            <h4 className="findingTitle">{names[finding.indicator_id] ?? finding.indicator_id}</h4>
            <div className="tagRow">
              <span className={finding.evidence_status === "insufficient" ? "tag tagWarning" : "tag"}>{evidenceStatusLabel(finding.evidence_status)}</span>
              <span className={`tag ${PERFORMANCE_TONE[finding.performance] ?? ""}`.trim()}>{performanceLabel(finding.performance)}</span>
            </div>
          </div>
          <p className="findingDesc prose">{finding.description}</p>
          {finding.rubric_focus?.length ? (
            <p className="findingMeta">评分关注：{finding.rubric_focus.join("、")}</p>
          ) : null}
          {finding.error_types?.length ? <p className="findingMeta">错误类型：{finding.error_types.join("、")}</p> : null}
          {diagnosticText(finding.diagnostic_details).map((line) => (
            <p className="findingMeta" key={line}>{line}</p>
          ))}
          {finding.limitations.length ? (
            <p className="findingMeta">限制：{finding.limitations.join("；")}</p>
          ) : (
            <p className="findingMeta">证据状态与表现分开判断：证据不足时不把“暂不定论”当成未掌握。</p>
          )}
          {finding.evidence_refs.length && onEvidence ? (
            <div className="row" style={{ marginTop: 8 }}>
              <EvidenceRefsButton
                refs={finding.evidence_refs}
                onOpen={onEvidence}
                context={evidenceContext}
                materialNames={materialNames}
              />
            </div>
          ) : null}
        </article>
      ))}
    </div>
  );
}

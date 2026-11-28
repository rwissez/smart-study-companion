import { useMemo } from "react";
import type { EvidenceRef, ProgressComparison } from "../../api/types";
import { progressLabel, ratioPercent } from "../../lib/format";

interface Props {
  items: ProgressComparison[];
  names: Record<string, string>;
  onEvidence?: (ref: EvidenceRef) => void;
}

const CLASSIFICATION_TONE: Record<string, string> = {
  improved: "badge badgeSuccess",
  partially_improved: "badge badgeSuccess",
  unchanged_or_declined: "badge badgeWarning",
  insufficient: "badge badgeUnknown",
};

function percent(value: number | null): string {
  const ratio = ratioPercent(value);
  return ratio === null ? "—" : `${Math.round(ratio)}%`;
}

/**
 * 进展时间线：每个可比口径一行，按“基线 → 当前”呈现分类结论。
 * 分类由服务端给出，前端只做排序（变化在前、证据不足在后），不自行推断。
 */
export function ProgressTimeline({ items, names, onEvidence }: Props) {
  const ordered = useMemo(() => {
    const weight = (classification: string) =>
      classification === "improved" || classification === "partially_improved"
        ? 0
        : classification === "unchanged_or_declined"
          ? 1
          : 2;
    return [...items].sort((left, right) => weight(left.classification) - weight(right.classification));
  }, [items]);

  const summary = useMemo(() => {
    let improved = 0;
    let declined = 0;
    let insufficient = 0;
    items.forEach((item) => {
      if (item.classification === "improved" || item.classification === "partially_improved") improved += 1;
      else if (item.classification === "unchanged_or_declined") declined += 1;
      else insufficient += 1;
    });
    return { improved, declined, insufficient };
  }, [items]);

  if (items.length === 0) {
    return <p className="muted small">暂无进展比较。需要至少两次同口径分析才会产生。</p>;
  }

  return (
    <div className="progressTimeline">
      <p className="small">
        有变化 {summary.improved} · 未改善 {summary.declined} · 证据不足 {summary.insufficient}
      </p>
      <ol className="progressTimelineList">
        {ordered.map((item) => {
          const indicator = item.scope.indicator_id;
          const label = indicator ? (names[indicator] ?? indicator) : item.scope.topic_id ?? item.scope.node_id;
          return (
            <li key={item.comparison_id} className="progressTimelineItem">
              <span className={CLASSIFICATION_TONE[item.classification] ?? "badge"}>{progressLabel(item.classification)}</span>
              <span className="progressTimelineBody">
                <span className="small">
                  {label}
                  {item.comparable ? (
                    <span className="tabular">
                      {" "}· {percent(item.baseline_value)} → {percent(item.current_value)}
                    </span>
                  ) : (
                    <span className="xs muted"> · 基线与当前不可比，只并列原值</span>
                  )}
                </span>
                {item.description ? <span className="xs muted">{item.description}</span> : null}
                {item.limitations.length ? <span className="xs muted">{item.limitations.join("；")}</span> : null}
                {onEvidence && item.evidence_refs.length ? (
                  <button type="button" className="btn btnSm btnGhost" onClick={() => onEvidence(item.evidence_refs[0])}>
                    查看依据
                  </button>
                ) : null}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

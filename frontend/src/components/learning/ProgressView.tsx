import type { EvidenceRef, ProgressComparison } from "../../api/types";
import { formatPercent, progressLabel } from "../../lib/format";

interface Props {
  items: ProgressComparison[];
  names: Record<string, string>;
  onEvidence?: (ref: EvidenceRef) => void;
}

const TONE: Record<string, string> = {
  improved: "tagAccent",
  partially_improved: "tagAccent",
  unchanged_or_declined: "tagWarning",
  insufficient: "tag",
};

/** 只展示 B 返回的 classification 与 comparable；不可比时不画趋势与差值。 */
export function ProgressView({ items, names, onEvidence }: Props) {
  if (items.length === 0) {
    return <p className="muted small">暂无进展比较。需要至少两次同口径分析才会产生。</p>;
  }
  return (
    <div className="tableWrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">观察指标</th>
            <th scope="col">分类</th>
            <th scope="col" className="numeric">基线</th>
            <th scope="col" className="numeric">当前</th>
            <th scope="col">可比性</th>
            <th scope="col">说明</th>
            <th scope="col">证据</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.comparison_id}>
              <td>{item.scope.indicator_id ? names[item.scope.indicator_id] ?? item.scope.indicator_id : "—"}</td>
              <td><span className={`tag ${TONE[item.classification] ?? ""}`.trim()}>{progressLabel(item.classification)}</span></td>
              <td className="numeric">{formatPercent(item.baseline_value)}</td>
              <td className="numeric">{formatPercent(item.current_value)}</td>
              <td className="small">{item.comparable ? "可比（同指标同口径）" : "不可比，不做差值"}</td>
              <td className="small">
                {item.description || "—"}
                {item.limitations.length ? <div className="xs muted">限制：{item.limitations.join("；")}</div> : null}
              </td>
              <td>
                {item.evidence_refs.length && onEvidence ? (
                  <button type="button" className="btn btnSm btnGhost" onClick={() => onEvidence(item.evidence_refs[0])}>
                    查看依据
                  </button>
                ) : (
                  <span className="xs muted">无引用</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

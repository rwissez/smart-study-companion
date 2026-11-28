import type { EvidenceRef, Metric } from "../../api/types";
import { formatFraction, formatPercent } from "../../lib/format";
import { metricNameLabel, unitLabel } from "../../lib/labels";

interface Props {
  metrics: Metric[];
  names: Record<string, string>;
  onEvidence?: (ref: EvidenceRef) => void;
}

/** 全部指标明细：名称、单位、数值、分子/分母、排除量、限制与证据入口。 */
export function MetricList({ metrics, names, onEvidence }: Props) {
  if (metrics.length === 0) {
    return <p className="muted small">本次分析没有返回指标。</p>;
  }
  return (
    <div className="tableWrap" style={{ borderTop: 0 }}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">指标</th>
            <th scope="col">观察指标</th>
            <th scope="col" className="numeric">数值</th>
            <th scope="col" className="numeric">分子 / 分母</th>
            <th scope="col" className="numeric">排除</th>
            <th scope="col">证据</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => (
            <tr key={metric.metric_id}>
              <td>
                <div className="small">{metricNameLabel(metric.name)}</div>
                <div className="xs muted">{unitLabel(metric.unit)}</div>
              </td>
              <td className="small">{metric.scope.indicator_id ? names[metric.scope.indicator_id] ?? metric.scope.indicator_id : "—"}</td>
              <td className="numeric">
                {metric.unit === "ratio" ? formatPercent(metric.value) : metric.value === null ? "无可用结果" : String(metric.value)}
              </td>
              <td className="numeric">{formatFraction(metric.numerator, metric.denominator)}</td>
              <td className="numeric">{metric.excluded_event_count}</td>
              <td>
                {metric.evidence_refs.length && onEvidence ? (
                  <button type="button" className="btn btnSm btnGhost" onClick={() => onEvidence(metric.evidence_refs[0])}>
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

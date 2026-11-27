import type { Metric } from "../../api/types";
import { formatFraction, formatPercent } from "../../lib/format";

interface Props {
  metrics: Metric[];
  names: Record<string, string>;
  order?: string[];
  height?: number;
  emptyText?: string;
}

interface Point {
  id: string;
  label: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  limitations: string[];
}

const CENTER = 210;
const CENTER_Y = 175;
const RADIUS = 105;

function polar(index: number, count: number, r: number): { x: number; y: number } {
  const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
  return { x: CENTER + r * Math.cos(angle), y: CENTER_Y + r * Math.sin(angle) };
}

/**
 * 只用同口径 valid_score_ratio；缺失值为 null 时保持缺失，不用 0 点补齐，
 * 也不闭合跨缺失维度的多边形。少于三项时退回带标签的条状展示。
 */
export function IndicatorRadar({ metrics, names, order, height = 320, emptyText = "暂无可用的得分率指标" }: Props) {
  const seen = new Set<string>();
  const points: Point[] = [];
  metrics.forEach((metric) => {
    if (metric.unit !== "ratio" || metric.name !== "valid_score_ratio") return;
    const indicatorId = metric.scope.indicator_id;
    if (!indicatorId || seen.has(indicatorId)) return;
    seen.add(indicatorId);
    points.push({
      id: indicatorId,
      label: names[indicatorId] ?? indicatorId,
      value: metric.value,
      numerator: metric.numerator,
      denominator: metric.denominator,
      limitations: metric.limitations,
    });
  });

  if (order && order.length > 0) {
    points.sort((a, b) => {
      const left = order.indexOf(a.id);
      const right = order.indexOf(b.id);
      return (left === -1 ? order.length : left) - (right === -1 ? order.length : right);
    });
  }

  const available = points.filter((point) => point.value !== null);

  if (points.length === 0) {
    return <p className="muted small">{emptyText}</p>;
  }

  const dataTable = (
    <details>
      <summary className="small muted" style={{ cursor: "pointer" }}>数据表</summary>
      <table className="radarTable" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th scope="col">观察指标</th>
            <th scope="col" className="numeric">得分率</th>
            <th scope="col" className="numeric">分子 / 分母</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.id}>
              <td>{point.label}</td>
              <td className="numeric">{formatPercent(point.value)}</td>
              <td className="numeric">{formatFraction(point.numerator, point.denominator)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );

  if (available.length === 0) {
    return (
      <div className="stackSm">
        <p className="muted small">暂无有效记录，指标保持缺失。</p>
        {dataTable}
      </div>
    );
  }

  if (points.length < 3) {
    return (
      <div className="stackSm">
        <p className="small muted">可用指标不足三项，改用条状展示（不与缺失维度连成多边形）。</p>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }} className="stackSm">
          {points.map((point) => (
            <li key={point.id}>
              <div className="rowBetween small">
                <span>{point.label}</span>
                <span className="tabular">{formatPercent(point.value)}</span>
              </div>
              <div className="progressTrack" style={{ marginTop: 4 }}>
                <div className="progressFill" style={{ width: `${(point.value ?? 0) * 100}%` }} />
              </div>
              <div className="xs muted tabular">{formatFraction(point.numerator, point.denominator)}</div>
            </li>
          ))}
        </ul>
        {dataTable}
      </div>
    );
  }

  const ringRadii = [0.25, 0.5, 0.75, 1];
  const allKnown = points.every((point) => point.value !== null);
  const polygon = allKnown
    ? points.map((point, index) => {
        const position = polar(index, points.length, RADIUS * (point.value ?? 0));
        return `${position.x.toFixed(1)},${position.y.toFixed(1)}`;
      }).join(" ")
    : null;

  return (
    <div className="radarWrap">
      <svg
        className="radarSvg"
        viewBox="0 0 420 360"
        style={{ maxHeight: height }}
        role="img"
        aria-label={`得分率雷达图，共 ${points.length} 个指标，可用 ${available.length} 个；刻度 0 到 100`}
      >
        {ringRadii.map((ratio) => (
          <circle
            key={ratio}
            cx={CENTER}
            cy={CENTER_Y}
            r={RADIUS * ratio}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={1}
          />
        ))}
        {points.map((point, index) => {
          const end = polar(index, points.length, RADIUS);
          return (
            <line
              key={`axis-${point.id}`}
              x1={CENTER}
              y1={CENTER_Y}
              x2={end.x}
              y2={end.y}
              stroke="var(--color-border)"
              strokeWidth={1}
            />
          );
        })}
        {polygon ? (
          <polygon points={polygon} fill="var(--color-chart-fill)" stroke="var(--color-primary)" strokeWidth={1.75} />
        ) : null}
        {points.map((point, index) => {
          const position = polar(index, points.length, RADIUS);
          const labelPosition = polar(index, points.length, RADIUS + 26);
          const anchor = Math.abs(labelPosition.x - CENTER) < 12 ? "middle" : labelPosition.x > CENTER ? "start" : "end";
          const shortLabel = point.label.length > 7 ? `${point.label.slice(0, 7)}…` : point.label;
          return (
            <g key={`label-${point.id}`}>
              <text x={labelPosition.x} y={labelPosition.y} textAnchor={anchor} fontSize={11} fill="var(--color-text-primary)">
                <title>{`${point.label}：${formatPercent(point.value)}`}</title>
                <tspan>{shortLabel}</tspan>
                <tspan x={labelPosition.x} dy={14} fontSize={10} fill="var(--color-text-secondary)">
                  {formatPercent(point.value)}
                </tspan>
              </text>
              {point.value !== null ? (
                <circle
                  cx={position.x}
                  cy={position.y}
                  r={3.5}
                  fill="var(--color-primary)"
                  stroke="var(--color-background)"
                  strokeWidth={1.5}
                >
                  <title>{`${point.label}：${formatPercent(point.value)}（${formatFraction(point.numerator, point.denominator)}）`}</title>
                </circle>
              ) : (
                <circle cx={position.x} cy={position.y} r={3} fill="none" stroke="var(--color-unknown)" strokeWidth={1.5}>
                  <title>{`${point.label}：无有效记录`}</title>
                </circle>
              )}
            </g>
          );
        })}
      </svg>
      <p className="radarCaption">
        刻度环依次为 25 / 50 / 75 / 100；得分率 = 有效得分 / 有效满分；空心点表示无有效记录，不跨越缺失维度。
        {!allKnown ? " 当前存在缺失指标，仅显示可用点。" : ""}
      </p>
      {dataTable}
    </div>
  );
}

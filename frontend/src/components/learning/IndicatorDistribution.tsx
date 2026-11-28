import { useMemo } from "react";
import type { DistributionBucket } from "../../api/types";
import { evidenceStatusLabel, performanceLabel } from "../../lib/format";

interface Props {
  buckets: DistributionBucket[];
  names: Record<string, string>;
}

interface Segment {
  performance: string;
  evidenceStatus: string;
  count: number;
}

interface IndicatorRow {
  indicatorId: string;
  total: number;
  rosterCount: number;
  segments: Segment[];
}

const SEGMENT_COLORS: Record<string, string> = {
  mastered: "var(--color-success)",
  emerging: "var(--color-primary)",
  weak: "var(--color-warning)",
  unknown: "var(--color-unknown)",
};

function segmentColor(segment: Segment): string {
  if (segment.performance === "mastered") return SEGMENT_COLORS.mastered;
  if (segment.performance === "weak") return SEGMENT_COLORS.weak;
  if (segment.evidenceStatus !== "sufficient") return SEGMENT_COLORS.unknown;
  return SEGMENT_COLORS.emerging;
}

/**
 * 班级表现分布的条形视图：每个观察指标一行，按“表现 × 证据状态”分段堆叠。
 * 覆盖不足的段用中性色，避免被读成“差”；精确计数保留在下方的明细表里。
 */
export function IndicatorDistribution({ buckets, names }: Props) {
  const rows = useMemo<IndicatorRow[]>(() => {
    const map = new Map<string, IndicatorRow>();
    buckets.forEach((bucket) => {
      const row = map.get(bucket.indicator_id) ?? {
        indicatorId: bucket.indicator_id,
        total: 0,
        rosterCount: bucket.roster_count || 0,
        segments: [],
      };
      row.total += bucket.count;
      row.rosterCount = Math.max(row.rosterCount, bucket.roster_count || 0);
      const existing = row.segments.find(
        (segment) => segment.performance === bucket.performance && segment.evidenceStatus === bucket.evidence_status,
      );
      if (existing) existing.count += bucket.count;
      else
        row.segments.push({
          performance: bucket.performance,
          evidenceStatus: bucket.evidence_status,
          count: bucket.count,
        });
      map.set(bucket.indicator_id, row);
    });
    return [...map.values()];
  }, [buckets]);

  if (rows.length === 0) {
    return <p className="muted small">服务端没有返回表现分布。</p>;
  }

  return (
    <div className="indicatorDistribution">
      {rows.map((row) => (
        <div key={row.indicatorId} className="indicatorDistRow">
          <div className="indicatorDistHead">
            <span className="small">{names[row.indicatorId] ?? row.indicatorId}</span>
            <span className="xs muted tabular">
              已观测 {row.total} / {row.rosterCount}
            </span>
          </div>
          <div className="indicatorDistBar" role="img" aria-label={`${names[row.indicatorId] ?? row.indicatorId}：已观测 ${row.total} 人，共 ${row.rosterCount} 人`}>
            {row.segments.map((segment, index) => {
              const width = row.rosterCount > 0 ? (segment.count / row.rosterCount) * 100 : 0;
              return (
                <span
                  key={index}
                  className="indicatorDistSegment"
                  style={{ width: `${width}%`, background: segmentColor(segment) }}
                  title={`${performanceLabel(segment.performance)}（${evidenceStatusLabel(segment.evidenceStatus)}）：${segment.count} 人`}
                />
              );
            })}
          </div>
          <div className="indicatorDistLegend xs muted">
            {row.segments.map((segment, index) => (
              <span key={index}>
                {performanceLabel(segment.performance)}（{evidenceStatusLabel(segment.evidenceStatus)}）{segment.count} 人
              </span>
            ))}
            {row.total < row.rosterCount ? <span>未产生可用记录 {row.rosterCount - row.total} 人</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

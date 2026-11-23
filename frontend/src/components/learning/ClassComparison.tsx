import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ClassResult, Metric } from "../../api/types";
import { formatFraction, formatNumber, formatRatio } from "../../lib/format";
import styles from "../../styles/ui.module.css";

interface Props {
  left: ClassResult;
  right: ClassResult;
  leftName: string;
  rightName: string;
  indicatorNames?: Record<string, string>;
  onClear?: () => void;
}

/**
 * 两班对比使用指标范围、统计口径及单位，避免混用同名指标。
 * 任一侧为空值时显示“无可用结果”，不画成 0，也不做班级排名。
 */
export function ClassComparison({ left, right, leftName, rightName, indicatorNames, onClear }: Props) {
  const keyOf = (metric: Metric) => JSON.stringify([
    metric.scope.node_id, metric.scope.indicator_id ?? null, metric.name, metric.unit,
  ]);
  const byKey = new Map([...left.metrics, ...right.metrics].map((metric) => [keyOf(metric), metric]));
  const matching = (metrics: Metric[], key: string) => {
    const matches = metrics.filter((metric) => keyOf(metric) === key);
    return matches.length === 1 ? matches[0] : null;
  };
  const sameScope = left.run_id === right.run_id && left.node_id === right.node_id &&
    left.dataset_id === right.dataset_id && left.catalog_version === right.catalog_version;
  const rows = sameScope ? Array.from(byKey, ([key, metric]) => ({
    key, name: metric.scope.indicator_id
      ? `${metric.name} · ${indicatorNames?.[metric.scope.indicator_id] ?? metric.scope.indicator_id}`
      : metric.name,
    left: matching(left.metrics, key), right: matching(right.metrics, key),
  })) : [];

  const chartable = rows.filter(
    (row) => row.left?.unit === "ratio" && row.left.value != null && row.right?.value != null,
  );

  return (
    <div className={`${styles.card}`}>
      <div className={styles.cardHead}>
        <span className={styles.cardTitle}>
          班级对比：{leftName} 与 {rightName}
        </span>
        {onClear ? (
          <button type="button" className={`${styles.button} ${styles.buttonGhost}`} onClick={onClear}>
            结束对比
          </button>
        ) : null}
      </div>

      <div className={styles.cardBody}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>统计项</th>
              <th>
                <div>{leftName}</div>
                <div className={`${styles.small} ${styles.muted}`}>
                  名册 {left.roster_count} · 有记录 {left.observed_student_count}
                </div>
              </th>
              <th>
                <div>{rightName}</div>
                <div className={`${styles.small} ${styles.muted}`}>
                  名册 {right.roster_count} · 有记录 {right.observed_student_count}
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={3} className={styles.muted}>
                  两侧都没有可用统计值，暂不对比。
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.key}>
                  <td>{row.name}</td>
                  {[row.left, row.right].map((metric, index) => (
                    <td key={index} className={styles.num}>
                      {metric?.unit === "ratio" ? formatRatio(metric.value) : formatNumber(metric?.value ?? null)}
                      {metric && <div className={styles.small}>{formatFraction(metric.numerator, metric.denominator)}</div>}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>

        {chartable.length > 0 ? (
          <div style={{ height: 240, marginTop: "var(--space-4)" }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartable.map((row) => ({
                  name: row.name,
                  [leftName]: row.left ? row.left.value : null,
                  [rightName]: row.right ? row.right.value : null,
                }))}
                margin={{ top: 8, right: 8, bottom: 0, left: -12 }}
              >
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#496078" }} />
                <YAxis
                  tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
                  tick={{ fontSize: 12, fill: "#496078" }}
                  domain={[0, 1]}
                />
                <Tooltip
                  formatter={(value: number) => formatRatio(value)}
                  contentStyle={{ borderRadius: 8, fontSize: 13 }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey={leftName} fill="#2458A6" radius={[4, 4, 0, 0]}>
                  <LabelList
                    dataKey={leftName}
                    position="top"
                    formatter={(value: number) => formatRatio(value)}
                    style={{ fontSize: 11, fill: "#496078" }}
                  />
                </Bar>
                <Bar dataKey={rightName} fill="#176A60" radius={[4, 4, 0, 0]}>
                  <LabelList
                    dataKey={rightName}
                    position="top"
                    formatter={(value: number) => formatRatio(value)}
                    style={{ fontSize: 11, fill: "#496078" }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className={`${styles.small} ${styles.muted}`} style={{ marginTop: "var(--space-3)" }}>
            只有两侧都有数值的统计项才会画图，其余在表格里显示为无可用结果。
          </p>
        )}

        <p className={`${styles.small} ${styles.muted}`} style={{ marginTop: "var(--space-3)" }}>
          两个节点教学范围不同，这里只并列同一节点内的数值，不作进退步判断。
        </p>
      </div>
    </div>
  );
}

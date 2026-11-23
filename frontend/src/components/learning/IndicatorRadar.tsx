import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip } from "recharts";
import type { Metric } from "../../api/types";
import styles from "./TeacherViews.module.css";

/** 只画具有相同口径、有效值及不同指标的结果；缺失值保持缺失。 */
export function IndicatorRadar({ metrics, names }: { metrics: Metric[]; names: Record<string, string> }) {
  const ratios = metrics.filter((item) =>
    item.unit === "ratio" &&
    (item.name === "valid_score_ratio" || item.name === "得分率") &&
    item.scope.indicator_id
  );
  const unique = ratios.filter((item) => ratios.filter((other) => other.scope.indicator_id === item.scope.indicator_id).length === 1);
  const available = unique.filter((item) => item.value !== null);
  const data = available.map((item) => ({
    id: item.scope.indicator_id!,
    label: names[item.scope.indicator_id!] ?? item.scope.indicator_id!,
    value: item.value! * 10,
    fraction: `${item.numerator ?? "—"} / ${item.denominator ?? "—"}`,
  }));
  return <div className={styles.radarContent}>
    {data.length >= 3 ? <div className={styles.radarChart}>
      <ResponsiveContainer width="100%" height={320}>
        <RadarChart data={data} outerRadius="65%">
          <PolarGrid stroke="#dbe4f2" />
          <PolarAngleAxis dataKey="label" tick={{ fontSize: 12, fill: "#41516c" }} />
          <PolarRadiusAxis angle={90} domain={[0, 10]} tickCount={3} tick={{ fontSize: 10 }} />
          <Radar dataKey="value" name="得分率换算" stroke="#2463eb" fill="#8caefc" fillOpacity={0.25} />
          <Tooltip formatter={(value: number) => `${value.toFixed(1)} / 10`} />
        </RadarChart>
      </ResponsiveContainer>
    </div> : <div className={styles.radarEmpty}>
      <div className={styles.radarOutline} aria-hidden="true">◇</div>
      <strong>{data.length ? "当前可用指标不足三项" : "暂无可用指标"}</strong>
    </div>}
    <div className={styles.metricRows}>
      {unique.map((metric) => <div className={styles.metricRow} key={metric.metric_id}>
        <span>{names[metric.scope.indicator_id!] ?? metric.scope.indicator_id}</span>
        <strong>{metric.value === null ? "无可用结果" : `${(metric.value * 10).toFixed(1)} / 10`}</strong>
        <span>{metric.numerator ?? "—"} / {metric.denominator ?? "—"}</span>
      </div>)}
      {unique.length === 0 && <p className={styles.muted}>暂无指标结果</p>}
    </div>
    <details className={styles.metaDetail}><summary>得分依据</summary><p>当前演示将得分率按 10 分制显示；分子、分母及正式口径以分析结果为准。</p></details>
  </div>;
}

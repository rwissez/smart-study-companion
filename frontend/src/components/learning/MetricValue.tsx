import type { Metric } from "../../api/types";
import { formatFraction, formatRatio } from "../../lib/format";
import styles from "../../styles/ui.module.css";

/** 单个统计项：数值、分子分母、范围与限制都摆出来，不做二次计算 */
export function MetricValue({ metric }: { metric: Metric }) {
  const isNull = metric.value === null;
  const display =
    metric.unit === "ratio"
      ? formatRatio(metric.value)
      : metric.value === null
        ? "无可用结果"
        : String(metric.value);

  return (
    <div className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
      <span className={styles.label}>{metric.name}</span>
      <span className={isNull ? styles.metricValueNull : styles.metricValue}>
        {isNull ? "无可用结果" : display}
      </span>
      <span className={styles.metricMeta}>
        分子 / 分母：{formatFraction(metric.numerator, metric.denominator)}
        {metric.excluded_event_count > 0
          ? `　排除 ${metric.excluded_event_count} 条异常记录`
          : ""}
      </span>
      {metric.limitations.length > 0 ? (
        <ul className={`${styles.small} ${styles.muted}`} style={{ margin: 0, paddingLeft: 16 }}>
          {metric.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

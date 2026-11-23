import type { ClassResult } from "../../api/types";
import { formatFraction, formatRatio } from "../../lib/format";
import styles from "../../styles/ui.module.css";

interface Props {
  result: ClassResult;
  name: string;
  active?: boolean;
  onSelect?: () => void;
}

/** 班级概况：名册人数与观测人数分开显示，统计值原样展示 */
export function ClassSummary({ result, name, active, onSelect }: Props) {
  const ratio = result.metrics.find((m) => m.unit === "ratio");

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`${styles.card} ${styles.cardPad} ${styles.stack}`}
      style={{
        textAlign: "left",
        borderColor: active ? "var(--primary)" : undefined,
        boxShadow: active ? "0 0 0 1px var(--primary)" : undefined,
      }}
      aria-pressed={active}
    >
      <div className={styles.rowBetween}>
        <span className={styles.cardTitle}>{name}</span>
        <span className={`${styles.badge} ${styles.badgeNeutral} ${styles.num}`}>
          {result.class_id}
        </span>
      </div>

      <div className={styles.row} style={{ gap: "var(--space-4)" }}>
        <span>
          <span className={styles.label}>名册</span>
          <br />
          <strong className={styles.num}>{result.roster_count} 人</strong>
        </span>
        <span>
          <span className={styles.label}>有记录</span>
          <br />
          <strong className={styles.num}>{result.observed_student_count} 人</strong>
        </span>
        <span>
          <span className={styles.label}>{ratio ? ratio.name : "统计值"}</span>
          <br />
          <strong className={styles.num}>
            {ratio ? formatRatio(ratio.value) : "无可用结果"}
          </strong>
        </span>
      </div>

      {ratio ? (
        <span className={styles.metricMeta}>
          分子 / 分母：{formatFraction(ratio.numerator, ratio.denominator)}
        </span>
      ) : null}
    </button>
  );
}

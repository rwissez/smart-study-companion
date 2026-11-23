import type { EvidenceRef, Finding } from "../../api/types";
import { evidenceStatusLabel, performanceLabel } from "../../lib/format";
import styles from "../../styles/ui.module.css";

interface Props {
  finding: Finding;
  indicatorName?: string;
  onOpenEvidence?: (ref: EvidenceRef) => void;
}

const performanceClass = {
  observed_strength: styles.badgeSuccess,
  needs_practice: styles.badgeWarn,
  undetermined: styles.badgeNeutral,
};

const evidenceClass = {
  sufficient: styles.badgeSuccess,
  insufficient: styles.badgeWarn,
  conflicting: styles.badgeDanger,
};

/**
 * 诊断发现卡：表现与证据是否充分分成两个标签，不合并成红黄绿等级。
 */
export function FindingCard({ finding, indicatorName, onOpenEvidence }: Props) {
  return (
    <div id={`finding-${finding.finding_id}`} tabIndex={-1} className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
      <div className={styles.rowBetween}>
        <span className={styles.label}>{indicatorName ?? finding.indicator_id}</span>
        <div className={styles.row}>
          <span className={`${styles.badge} ${performanceClass[finding.performance]}`}>
            {performanceLabel[finding.performance]}
          </span>
          <span className={`${styles.badge} ${evidenceClass[finding.evidence_status]}`}>
            {evidenceStatusLabel[finding.evidence_status]}
          </span>
        </div>
      </div>

      <p>{finding.description}</p>

      {finding.limitations.length > 0 ? (
        <ul className={`${styles.small} ${styles.muted}`} style={{ margin: 0, paddingLeft: 16 }}>
          {finding.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      ) : null}

      {finding.evidence_refs.length > 0 && onOpenEvidence ? (
        <div className={styles.row}>
          {finding.evidence_refs.map((ref) => (
            <button
              key={`${ref.kind}-${ref.id}`}
              type="button"
              className={`${styles.button} ${styles.buttonGhost}`}
              onClick={() => onOpenEvidence(ref)}
            >
              查看依据 · {ref.kind === "record" ? "作答记录" : "统计项"} {ref.id}
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.small} aria-live="polite">
          暂无可查看的引用
        </p>
      )}
    </div>
  );
}

import type { EvidenceRef, Finding } from "../../api/types";
import { evidenceStatusLabel, formatAnalysisLimitation, formatEvidenceLabel, formatFindingTitle } from "../../lib/format";
import styles from "../../styles/ui.module.css";

interface Props {
  finding: Finding;
  indicatorName?: string;
  onOpenEvidence?: (ref: EvidenceRef) => void;
}

const evidenceClass = {
  sufficient: styles.badgeSuccess,
  insufficient: styles.badgeWarn,
  conflicting: styles.badgeDanger,
};

/**
 * 诊断发现卡：结论和证据状态分别展示。
 */
export function FindingCard({ finding, indicatorName, onOpenEvidence }: Props) {
  return (
    <div id={`finding-${finding.finding_id}`} tabIndex={-1} className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
      <div className={styles.rowBetween}>
        <strong>{formatFindingTitle(finding, indicatorName)}</strong>
        <span className={`${styles.badge} ${evidenceClass[finding.evidence_status]}`}>
          {evidenceStatusLabel[finding.evidence_status]}
        </span>
      </div>

      <p>{finding.description}</p>

      {finding.limitations.length > 0 ? (
        <ul className={`${styles.small} ${styles.muted}`} style={{ margin: 0, paddingLeft: 16 }}>
          {finding.limitations.map((text) => (
            <li key={text}>{formatAnalysisLimitation(text)}</li>
          ))}
        </ul>
      ) : null}

      {finding.evidence_refs.length > 0 && onOpenEvidence ? (
        <div className={styles.row}>
          {finding.evidence_refs.map((ref, index) => (
            <button
              key={`${ref.kind}-${ref.id}`}
              type="button"
              className={`${styles.button} ${styles.buttonGhost}`}
              onClick={() => onOpenEvidence(ref)}
            >
              查看依据 · {formatEvidenceLabel(ref, index + 1)}
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

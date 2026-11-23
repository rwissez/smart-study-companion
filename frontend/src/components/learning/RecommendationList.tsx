import { Link } from "react-router-dom";
import { useState, type ReactNode } from "react";
import type { Catalog, Recommendation } from "../../api/types";
import { generationMethodLabel, reviewStatusLabel } from "../../lib/format";
import styles from "../../styles/ui.module.css";

interface Props {
  recommendations: Recommendation[];
  materials?: Catalog["materials"];
  teacher?: boolean;
  renderActions?: (recommendation: Recommendation) => ReactNode;
  onOpenMaterial: (materialId: string) => void;
  onOpenFinding?: (findingId: string) => void;
  curriculumLink?: (indicatorId: string) => string;
}

/**
 * 建议列表：按 order 显示材料、用途、分步提示与观察条件。
 * 提示按层级逐步展开，一次只多给一条，不直接给答案。
 */
export function RecommendationList({ recommendations, materials, teacher, renderActions, onOpenMaterial, onOpenFinding, curriculumLink }: Props) {
  const [hints, setHints] = useState<Record<string, number>>({});

  if (recommendations.length === 0) {
    return (
      <div className={`${styles.state} ${styles.stateEmpty}`} role="status">
        <span className={styles.stateTitle}>暂无学习建议</span>
        <span>该学生在这个节点还没有生成建议，可以稍后重试或换一个节点。</span>
      </div>
    );
  }

  const showHint = (recId: string, index: number) => {
    setHints((prev) => ({ ...prev, [recId]: index + 1 }));
  };

  return (
    <div className={styles.stackLg}>
      {recommendations.map((rec) => {
        return (
          <article key={rec.recommendation_id} className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
            <div className={styles.rowBetween}>
              <span className={styles.cardTitle}>{rec.goal}</span>
              <div className={styles.row}>
                {teacher && <span className={`${styles.badge} ${styles.badgeNeutral}`}>
                  {rec.review_status === "validated" ? "已发布" : reviewStatusLabel[rec.review_status]}
                </span>}
                <span className={`${styles.badge} ${styles.badgeInfo}`}>
                  {generationMethodLabel[rec.generation_method]}
                </span>
              </div>
            </div>

            <p className={styles.muted}>{rec.reason}</p>
            {(onOpenFinding || curriculumLink) && <div className={styles.row}>
              {onOpenFinding && rec.finding_ids.map((id, index) =>
                <button key={id} className={styles.button} onClick={() => onOpenFinding(id)}>查看关联诊断{rec.finding_ids.length > 1 ? " " + (index + 1) : ""}</button>)}
              {curriculumLink && <Link to={curriculumLink(rec.indicator_id)}>查看课程要求</Link>}
            </div>}

            <ol className={styles.steps} style={{ paddingLeft: 18 }}>
              {rec.activities.map((activity) => {
                const material = materials?.find((item) => item.material_id === activity.material_id);
                const hintKey = `${rec.recommendation_id}:${activity.order}`;
                const activityShown = hints[hintKey] ?? 0;
                return (
                  <li key={`${rec.recommendation_id}-${activity.order}`} style={{ marginBottom: 12 }}>
                    <div className={styles.row}>
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonGhost}`}
                        onClick={() => onOpenMaterial(activity.material_id)}
                      >
                        {material?.material_type === "question" ? "查看题目" : "打开学习材料"}
                      </button>
                      {activity.previously_attempted ? (
                        <span className={`${styles.badge} ${styles.badgeWarn}`}>已做过</span>
                      ) : null}
                    </div>
                    <p style={{ margin: "4px 0" }}>{activity.purpose}</p>
                    {material && <p><strong>{material.content.length > 160 ? material.content.slice(0, 160) + "…" : material.content}</strong></p>}
                    {material?.options?.map((option) => <div key={option}>{option}</div>)}

                    {activity.hint_levels.length > 0 ? (
                      <div className={styles.stack}>
                        {activity.hint_levels.slice(0, activityShown).map((hint, index) => (
                          <p key={hint} className={styles.hint}>
                            提示 {index + 1}：{hint}
                          </p>
                        ))}
                        {activityShown < activity.hint_levels.length ? (
                          <div>
                            <button
                              type="button"
                              className={styles.button}
                              onClick={() => showHint(hintKey, activityShown)}
                            >
                              {activityShown === 0 ? "查看第一条提示" : "继续查看提示"}
                            </button>
                          </div>
                        ) : null}
                      </div>
                    ) : null}

                    {activity.observation_points.length > 0 ? (
                      <p className={`${styles.small} ${styles.muted}`}>
                        观察点：{activity.observation_points.join("；")}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ol>
            {rec.activities.length === 0 && <p>暂无对应材料，等待教师补充。</p>}

            {rec.adjustment_conditions.length > 0 ? (
              <div className={styles.stack}>
                <span className={styles.label}>后续调整条件</span>
                <ul className={`${styles.small} ${styles.muted}`} style={{ margin: 0, paddingLeft: 16 }}>
                  {rec.adjustment_conditions.map((condition) => (
                    <li key={condition.observable_condition}>
                      看到「{condition.observable_condition}」时：{condition.next_action}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {renderActions?.(rec)}
          </article>
        );
      })}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCatalog, getRecommendations, isDemo } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { RecommendationList } from "../../components/learning/RecommendationList";
import { EvidencePanel, type EvidenceTarget } from "../../components/evidence/EvidencePanel";
import { StatePanel } from "../../components/feedback/StatePanel";
import styles from "../../styles/ui.module.css";

/** 学生学习计划：目标、活动、材料与分步提示 */
export function StudentPlanPage() {
  const { session } = useSession();
  const { runId, nodeId, nodes, setNodeId } = useScope();
  const studentId = session?.scope.student_id ?? null;
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  useEffect(() => setEvidence(null), [runId, nodeId, studentId]);

  const recommendations = useQuery({
    queryKey: queryKeys.recommendations(session, runId, studentId, nodeId),
    queryFn: () => getRecommendations(runId as string, studentId as string, nodeId as string),
    enabled: Boolean(session && runId && studentId && nodeId),
    retry: false,
  });

  const catalogVersion = recommendations.data?.catalog_version;
  const catalog = useQuery({
    queryKey: queryKeys.catalog(catalogVersion ?? null),
    queryFn: () => getCatalog(catalogVersion),
    enabled: isDemo && Boolean(catalogVersion),
  });
  const nodeName = nodes.find((n) => n.node_id === nodeId)?.name ?? "未选择节点";

  return (
    <div className={styles.stackLg}>
      <div className={`${styles.card} ${styles.cardPad} ${styles.rowBetween}`}>
        <div className={styles.stack}>
          <h2>我的学习计划</h2>
          <span className={`${styles.small} ${styles.muted}`}>
            当前节点：{nodeName}　·　提示会一条一条出现，先自己想一步再看下一条
          </span>
        </div>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>切换节点</span>
          <select
            className={styles.select}
            value={nodeId ?? ""}
            onChange={(e) => setNodeId(e.target.value)}
          >
            {nodes.map((n) => (
              <option key={n.node_id} value={n.node_id}>
                {n.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {recommendations.isLoading ? <StatePanel kind="loading" title="正在读取学习安排" /> : null}
      {recommendations.error ? (
        <StatePanel
          kind="error"
          message={(recommendations.error as Error).message}
          onRetry={() => recommendations.refetch()}
          action={
            <Link className={styles.button} to="/student/home">
              返回首页
            </Link>
          }
        />
      ) : null}

      {recommendations.data ? (
        recommendations.data.pending_review && recommendations.data.recommendations.length === 0 ? (
          <StatePanel kind="info" title="学习建议正在复核" message="教师核对材料和建议后，才能在这里查看。" />
        ) : (
        <RecommendationList
          key={`${runId}:${nodeId}:${studentId}`}
          recommendations={recommendations.data.recommendations}
          materials={recommendations.data.materials ?? catalog.data?.materials}
          onOpenMaterial={(materialId) => setEvidence({ kind: "material", id: materialId })}
        />
        )
      ) : null}
      <div className={styles.row}>
        <Link className={styles.button} to="/student/chat">就我的学习计划提问</Link>
      </div>

      <EvidencePanel
        target={evidence}
        runId={runId}
        catalogVersion={recommendations.data?.catalog_version ?? null}
        onClose={() => setEvidence(null)}
      />
    </div>
  );
}

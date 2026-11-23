import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCatalog, getRecommendations, getStudentResult } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { MetricValue } from "../../components/learning/MetricValue";
import { FindingCard } from "../../components/learning/FindingCard";
import { StatePanel } from "../../components/feedback/StatePanel";
import styles from "../../styles/ui.module.css";
import page from "../../styles/pages.module.css";

/** 学生首页：我的学习表现、需要继续观察、下一步 */
export function StudentHomePage() {
  const { session } = useSession();
  const { runId, nodeId, nodes, setNodeId } = useScope();
  const studentId = session?.scope.student_id ?? null;

  const result = useQuery({
    queryKey: queryKeys.studentResult(session, runId, studentId, nodeId),
    queryFn: () => getStudentResult(runId as string, studentId as string, nodeId as string),
    enabled: Boolean(session && runId && studentId && nodeId),
    retry: false,
  });

  const recommendations = useQuery({
    queryKey: queryKeys.recommendations(session, runId, studentId, nodeId),
    queryFn: () => getRecommendations(runId as string, studentId as string, nodeId as string),
    enabled: Boolean(session && runId && studentId && nodeId),
    retry: false,
  });

  const catalog = useQuery({
    queryKey: queryKeys.catalog(null),
    queryFn: () => getCatalog(),
    staleTime: 300_000,
    retry: false,
  });

  const indicatorNames: Record<string, string> = {};
  for (const item of catalog.data?.indicators ?? []) {
    indicatorNames[item.indicator_id] = item.description;
  }

  const nodeName = nodes.find((n) => n.node_id === nodeId)?.name ?? "未选择节点";
  const nextTask = recommendations.data?.recommendations[0] ?? null;

  return (
    <div className={styles.stackLg}>
      <div className={`${styles.card} ${styles.cardPad} ${styles.rowBetween}`}>
        <div className={styles.stack}>
          <h2>我现在的学习情况</h2>
          <span className={`${styles.small} ${styles.muted}`}>
            当前节点：{nodeName}　·　这里只显示你自己的记录
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

      {result.isLoading ? <StatePanel kind="loading" title="正在读取你的学习记录" /> : null}
      {result.error ? (
        <StatePanel
          kind="error"
          message={(result.error as Error).message}
          onRetry={() => result.refetch()}
        />
      ) : null}

      {result.data ? (
        <div className={page.grid3}>
          <section className={styles.stack}>
            <h3>我的学习表现</h3>
            {result.data.metrics.length === 0 ? (
              <StatePanel
                kind="empty"
                title="这个节点还没有统计"
                message={result.data.limitations.join("；") || "没有留下可统计的记录。"}
              />
            ) : (
              <div className={styles.stack}>
                {result.data.metrics.map((metric) => (
                  <MetricValue key={metric.metric_id} metric={metric} />
                ))}
              </div>
            )}
          </section>

          <section className={styles.stack}>
            <h3>本次测验的学习发现</h3>
            {result.data.findings.length === 0 ? (
              <StatePanel kind="empty" title="暂时没有需要特别关注的内容" />
            ) : (
              <div className={styles.stack}>
                {result.data.findings.map((finding) => (
                  <FindingCard key={finding.finding_id} finding={finding} indicatorName={indicatorNames[finding.indicator_id]} />
                ))}
              </div>
            )}
          </section>

          <section className={styles.stack}>
            <h3>下一步</h3>
            {nextTask ? (
              <div className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
                <strong>{nextTask.goal}</strong>
                <p className={`${styles.small} ${styles.muted}`}>{nextTask.reason}</p>
                <Link className={styles.button} to="/student/plan">
                  查看学习计划
                </Link>
              </div>
            ) : (
              <StatePanel
                kind="empty"
                title="暂时没有安排"
                message="老师还没有为这个节点生成学习建议。"
              />
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}

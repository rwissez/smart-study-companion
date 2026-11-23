import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { demoRecommendationHistory, demoSaveRecommendation, isDemo, getCatalog, getRecommendations, getStudentResult } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { FindingCard } from "../../components/learning/FindingCard";
import { IndicatorRadar } from "../../components/learning/IndicatorRadar";
import view from "../../components/learning/TeacherViews.module.css";
import { RecommendationEditor } from "../../components/learning/RecommendationEditor";
import { RecommendationList } from "../../components/learning/RecommendationList";
import { EvidencePanel, type EvidenceTarget } from "../../components/evidence/EvidencePanel";
import { StatePanel } from "../../components/feedback/StatePanel";
import styles from "../../styles/ui.module.css";
import page from "../../styles/pages.module.css";

/** 教师学生画像：表现、诊断、建议与证据在同一条路径上 */
export function StudentProfilePage() {
  const queryClient = useQueryClient();
  const { studentId } = useParams();
  const [search] = useSearchParams();
  const classId = search.get("classId");
  const { session } = useSession();
  const { runId, nodeId, nodes, ready, bootstrapError } = useScope();
  const [tab, setTab] = useState<"overview" | "records" | "recommendations">("overview");
  const [indicator, setIndicator] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);

  useEffect(() => {
    setEvidence(null);
    setIndicator(null);
    setTab("overview");
  }, [runId, nodeId, studentId]);

  const studentQuery = useQuery({
    queryKey: queryKeys.studentResult(session, runId, studentId ?? null, nodeId),
    queryFn: () => getStudentResult(runId as string, studentId as string, nodeId as string),
    enabled: Boolean(session && runId && nodeId && studentId),
    retry: false,
  });

  const recommendationKey = queryKeys.recommendations(session, runId, studentId ?? null, nodeId);
  const recommendQuery = useQuery({
    queryKey: recommendationKey,
    queryFn: () => getRecommendations(runId as string, studentId as string, nodeId as string),
    enabled: Boolean(session && runId && nodeId && studentId),
    retry: false,
  });

  const catalogVersion = studentQuery.data?.catalog_version ?? null;
  const catalogQuery = useQuery({
    queryKey: queryKeys.catalog(catalogVersion),
    queryFn: () => getCatalog(catalogVersion),
    enabled: Boolean(catalogVersion),
    staleTime: 300_000,
    retry: false,
  });

  const indicatorNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const item of catalogQuery.data?.indicators ?? []) {
      map[item.indicator_id] = item.description;
    }
    return map;
  }, [catalogQuery.data]);

  const nodeName = nodes.find((n) => n.node_id === nodeId)?.name ?? nodeId ?? "";

  const findingItems = (studentQuery.data?.findings ?? []).filter(
    (finding) => !indicator || finding.indicator_id === indicator,
  );
  const records = Array.from(new Map(
    [...(studentQuery.data?.metrics ?? []), ...(studentQuery.data?.findings ?? [])]
      .flatMap((item) => item.evidence_refs)
      .filter((ref) => ref.kind === "record")
      .map((ref) => [ref.id, ref] as const),
  ).values());

  if (bootstrapError) {
    return <StatePanel kind="error" title="范围没有建立起来" message={bootstrapError} />;
  }

  if (!studentId) {
    return <StatePanel kind="empty" title="没有指定学生" message="请从班级名册进入学生画像。" />;
  }

  return (
    <div className={styles.stackLg}>

      <div className={view.head}>
        <div><div className={view.breadcrumbs}>班级总览 / {classId ?? "班级"} / {studentId}</div>
          <h2>学生学习画像 · {studentId}</h2><span className={view.muted}>{nodeName}</span></div>
        <div className={styles.row}>
          <Link className={styles.button} to={`/teacher/classes?${new URLSearchParams(classId ? { classId } : {})}`}>返回班级</Link>
          <Link className={styles.button} to={`/teacher/chat?${new URLSearchParams({ studentId })}`}>就此学生提问</Link>
        </div>
      </div>
      <div className={view.pageTabs}>
        <button className={tab === "overview" ? view.activePageTab : ""} onClick={() => setTab("overview")}>学情概览</button>
        <button className={tab === "records" ? view.activePageTab : ""} onClick={() => setTab("records")}>作答记录</button>
        <button className={tab === "recommendations" ? view.activePageTab : ""} onClick={() => setTab("recommendations")}>学习建议</button>
      </div>
      {tab === "records" && <section className={view.panel}>
        <div className={view.panelHeader}><h2>关联作答记录</h2></div>
        <p className={view.muted}>当前展示分析结果引用的作答记录；完整名录待接口提供。</p>
        {records.length === 0 && <StatePanel kind="empty" title="暂无可查看的作答引用" />}
        {records.map((ref) => <div className={view.findingTop} key={ref.id}>
          <strong>{ref.id}</strong><button onClick={() => setEvidence(ref)}>查看作答 ›</button></div>)}
      </section>}

      {!ready ? <StatePanel kind="loading" title="正在确定运行与节点" /> : null}

      {ready && tab !== "records" ? (
        <div className={view.profileLayout}>
          {tab === "overview" && <section className={view.panel}>
            <div className={view.panelHeader}><h2>课程观察指标</h2><span className={view.muted}>得分率 · 10 分制</span></div>
            {studentQuery.isLoading && <StatePanel kind="loading" title="正在读取学生结果" />}
            {studentQuery.error && <StatePanel kind="error" message={(studentQuery.error as Error).message} onRetry={() => studentQuery.refetch()} />}
            {studentQuery.data && <IndicatorRadar metrics={studentQuery.data.metrics} names={indicatorNames} />}
          </section>}
          <div className={styles.stackLg}>
            <section className={page.section}>
              <div className={page.sectionHead}>
                <h2>诊断发现</h2>

              </div>

              {findingItems.length === 0 ? (
                <StatePanel kind="empty" title="暂无可展示的诊断" message="该指标下还没有形成结论。" />
              ) : (
                <div className={styles.stack}>
                  {findingItems.map((finding) => (
                    <FindingCard
                      key={finding.finding_id}
                      finding={finding}
                      indicatorName={indicatorNames[finding.indicator_id]}
                      onOpenEvidence={setEvidence}
                    />
                  ))}
                </div>
              )}
            </section>
            <section className={page.section}>
              <div className={page.sectionHead}>
                <h2>学习建议</h2>
              </div>

              {recommendQuery.isLoading ? <StatePanel kind="loading" title="正在读取建议" /> : null}
              {recommendQuery.error ? (
                <StatePanel
                  kind="error"
                  message={(recommendQuery.error as Error).message}
                  onRetry={() => recommendQuery.refetch()}
                />
              ) : null}
              {recommendQuery.data ? (
                <RecommendationList
                  recommendations={recommendQuery.data.recommendations}
                  onOpenMaterial={(materialId) => setEvidence({ kind: "material", id: materialId })}
                  onOpenFinding={(id) => {
                    setIndicator(null);
                    requestAnimationFrame(() => document.getElementById(`finding-${id}`)?.focus());
                  }}
                  curriculumLink={(id) => `/teacher/curriculum?${new URLSearchParams({ indicatorId: id })}`}
                />
              ) : null}
              {isDemo && recommendQuery.data?.recommendations.map((recommendation) => (
                <RecommendationEditor key={`${runId}:${studentId}:${nodeId}:${recommendation.recommendation_id}`}
                  recommendation={recommendation}
                  history={demoRecommendationHistory(recommendQuery.data, recommendation.recommendation_id)}
                  onSave={(next, reason) => {
                    const bundle = recommendQuery.data;
                    if (!bundle) return [];
                    queryClient.setQueryData(recommendationKey, demoSaveRecommendation(bundle, next, reason));
                    return demoRecommendationHistory(bundle, next.recommendation_id);
                  }} />
              ))}
            </section>

          </div>
        </div>
      ) : null}

      <EvidencePanel
        target={evidence}
        runId={runId}
        catalogVersion={catalogVersion}
        onClose={() => setEvidence(null)}
      />
    </div>
  );
}

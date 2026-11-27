import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import { getCatalog, getPlan, getProgress, getStudentProfile } from "../../api/service";
import type { EvidenceRef, PlanView, StudentResultView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { StatePanel } from "../../components/feedback/StatePanel";
import { EvidenceDrawer } from "../../components/evidence/EvidenceDrawer";
import { IndicatorRadar } from "../../components/learning/IndicatorRadar";
import { ProgressView } from "../../components/learning/ProgressView";
import { performanceLabel } from "../../lib/format";

/** 学生个人画像：左侧雷达，右侧学习重点，下方进展。只读本人正式数据。 */
export function StudentProfilePage() {
  const { scopeKey, session } = useSession();
  const datasetId = scopeKey.datasetId;
  const studentId = session?.scope.student_id ?? null;
  const navigate = useNavigate();
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);

  const profile = useQuery<{ result: StudentResultView; freshness: string; limitations: string[] }>({
    queryKey: queryKeys.studentProfile(scopeKey, studentId ?? "", null, null),
    queryFn: () => getStudentProfile(datasetId as string, studentId as string),
    enabled: Boolean(datasetId && studentId),
  });

  const catalog = useQuery({
    queryKey: queryKeys.catalog(scopeKey, profile.data?.result.catalog_version ?? null),
    queryFn: () => getCatalog(profile.data?.result.catalog_version ?? null),
    enabled: Boolean(datasetId),
  });

  const plan = useQuery<PlanView>({
    queryKey: queryKeys.plan(scopeKey, studentId ?? "", null, null),
    queryFn: () => getPlan(datasetId as string, studentId as string, {}),
    enabled: Boolean(datasetId && studentId),
  });

  const progress = useQuery({
    queryKey: queryKeys.progress(scopeKey, studentId ?? "", 1),
    queryFn: () => getProgress(datasetId as string, studentId as string, { page: 1, pageSize: 20 }),
    enabled: Boolean(datasetId && studentId),
  });

  const names = useMemo(() => {
    const map: Record<string, string> = {};
    catalog.data?.indicators.forEach((indicator) => { map[indicator.indicator_id] = indicator.description; });
    return map;
  }, [catalog.data]);
  const order = useMemo(() => (catalog.data?.indicators ?? []).map((indicator) => indicator.indicator_id), [catalog.data]);

  const result = profile.data?.result;
  const suggestion = plan.data?.plan.recommendations[0];

  if (!datasetId || !studentId) {
    return <StatePanel kind="empty" title="学生会话缺少数据集或学生身份" message="请回到身份入口重新选择。" />;
  }

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">个人画像</h1>
          <p className="pageSub">
            {result ? "本人正式学情 · 只读 · 不显示他人或班级排名" : "读取本人正式学情"}
          </p>
        </div>
        <div className="pageActions">
          <button type="button" className="btn btnPrimary" onClick={() => navigate("/student/plan")}>继续学习</button>
          <button type="button" className="btn" onClick={() => navigate("/student/chat")}>询问湛知</button>
        </div>
      </div>

      {profile.isPending ? <StatePanel kind="loading" title="正在读取个人画像" /> : null}
      {profile.isError ? (
        <StatePanel
          kind={errorCode(profile.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
          title={errorCode(profile.error) === "RESULT_NOT_READY" ? "这个节点还没有你的正式学情" : "个人画像读取失败"}
          message={toErrorMessage(profile.error)}
          requestId={errorRequestId(profile.error)}
          onRetry={() => void profile.refetch()}
        />
      ) : null}

      {result ? (
        <>
          <section className="grid2" aria-label="学习表现与重点">
            <div>
              <div className="sectionHead">
                <h2 className="sectionTitle">学习表现</h2>
                <span className="sectionHint">有效得分率 0—100</span>
              </div>
              <IndicatorRadar metrics={result.metrics} names={names} order={order} />
            </div>
            <div className="stack">
              <div className="sectionHead">
                <h2 className="sectionTitle">学习重点</h2>
                <span className="sectionHint">证据不足的内容不标成“差”</span>
              </div>
              {result.findings.length === 0 ? (
                <p className="muted small">这次分析没有给出诊断发现。</p>
              ) : (
                <ul className="stackSm" style={{ listStyle: "none", padding: 0 }}>
                  {result.findings.map((finding) => (
                    <li key={finding.finding_id}>
                      <div className="rowBetween">
                        <strong>{names[finding.indicator_id] ?? finding.indicator_id}</strong>
                        <span className="tag">{performanceLabel(finding.performance)}</span>
                      </div>
                      <p className="prose">{finding.description}</p>
                      {finding.limitations.length ? <p className="xs muted">限制：{finding.limitations.join("；")}</p> : null}
                      {finding.evidence_refs.length ? (
                        <button
                          type="button"
                          className="btn btnSm btnGhost"
                          onClick={() => setEvidence(finding.evidence_refs[0])}
                        >
                          查看依据
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {result.summary ? <p className="prose">{result.summary}</p> : null}
              {result.limitations.length ? <p className="small muted">限制：{result.limitations.join("；")}</p> : null}
            </div>
          </section>

          <section className="section" aria-label="学习建议">
            <div className="sectionHead">
              <h2 className="sectionTitle">学习建议</h2>
              <span className="sectionHint">来自正式学习方案，学生安全投影</span>
            </div>
            {plan.isPending ? <StatePanel kind="loading" title="正在读取学习建议" /> : null}
            {plan.isError ? (
              <StatePanel
                kind={errorCode(plan.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
                title={errorCode(plan.error) === "RESULT_NOT_READY" ? "还没有正式学习方案" : "学习建议读取失败"}
                message={toErrorMessage(plan.error)}
              />
            ) : null}
            {suggestion ? (
              <div className="reading stack">
                <p className="prose">{suggestion.student_intro || suggestion.goal}</p>
                <p className="small muted">{plan.data?.plan.task_groups.length ?? 0} 个任务组已按当前学情安排</p>
                <div className="row">
                  <button type="button" className="btn btnPrimary" onClick={() => navigate("/student/plan")}>查看学习计划</button>
                </div>
              </div>
            ) : plan.data ? (
              <p className="small muted">方案没有返回建议文本。</p>
            ) : null}
          </section>

          <section className="section" aria-label="学习进展">
            <div className="sectionHead">
              <h2 className="sectionTitle">学习进展</h2>
              <span className="sectionHint">分类与可比性由服务端返回</span>
            </div>
            {progress.isPending ? <StatePanel kind="loading" title="正在读取进展" /> : null}
            {progress.isError ? (
              <StatePanel kind="error" title="进展读取失败" message={toErrorMessage(progress.error)} />
            ) : null}
            {progress.data ? <ProgressView items={progress.data.items} names={names} onEvidence={setEvidence} /> : null}
          </section>
        </>
      ) : null}

      <EvidenceDrawer
        refItem={evidence}
        context={{ student_id: studentId }}
        onClose={() => setEvidence(null)}
      />
    </div>
  );
}

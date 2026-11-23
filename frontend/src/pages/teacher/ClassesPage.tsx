import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCatalog, getClassResult, listClasses, listRoster } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { IndicatorRadar } from "../../components/learning/IndicatorRadar";
import { ClassComparison } from "../../components/learning/ClassComparison";
import { EvidencePanel, type EvidenceTarget } from "../../components/evidence/EvidencePanel";
import { StatePanel } from "../../components/feedback/StatePanel";
import { formatRatio } from "../../lib/format";
import ui from "../../styles/ui.module.css";
import view from "../../components/learning/TeacherViews.module.css";

const classNames: Record<string, string> = { "DEMO-A": "甲班", "DEMO-B": "乙班" };

export function ClassesPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const { session } = useSession();
  const { datasetId, runId, nodeId, ready, bootstrapError } = useScope();
  const classesQuery = useQuery({ queryKey: queryKeys.classes(session, datasetId), queryFn: () => listClasses(datasetId!), enabled: Boolean(session && datasetId), retry: false });
  const classes = classesQuery.data ?? [];
  const classId = classes.find((item) => item.class_id === search.get("classId"))?.class_id ?? classes[0]?.class_id ?? null;
  const detail = search.get("view") === "detail";
  const setClass = (id: string) => { setFilter(""); setPage(1); setSearch({ classId: id }); };
  useEffect(() => { setEvidence(null); }, [runId, nodeId, classId]);
  const result = useQuery({ queryKey: queryKeys.classResult(session, runId, classId, nodeId), queryFn: () => getClassResult(runId!, classId!, nodeId!), enabled: Boolean(session && runId && classId && nodeId), retry: false });
  const otherClass = classes.find((item) => item.class_id !== classId);
  const comparing = search.get("view") === "compare";
  const comparison = useQuery({ queryKey: queryKeys.classResult(session, runId, otherClass?.class_id ?? null, nodeId), queryFn: () => getClassResult(runId!, otherClass!.class_id, nodeId!), enabled: Boolean(comparing && runId && nodeId && otherClass), retry: false });
  const roster = useQuery({ queryKey: queryKeys.roster(session, datasetId, classId, page), queryFn: () => listRoster(datasetId!, classId, page), enabled: Boolean(session && datasetId && classId), retry: false });
  const catalog = useQuery({ queryKey: queryKeys.catalog(result.data?.catalog_version ?? null), queryFn: () => getCatalog(result.data?.catalog_version), enabled: Boolean(result.data?.catalog_version), retry: false });
  const names = Object.fromEntries((catalog.data?.indicators ?? []).map((item) => [item.indicator_id, item.description]));
  const visible = (roster.data?.items ?? []).filter((item) => item.student_id.toUpperCase().includes(filter.trim().toUpperCase()));
  const title = classNames[classId ?? ""] ?? classId ?? "班级";
  const goStudent = (id: string) => navigate(`/teacher/students/${encodeURIComponent(id)}?${new URLSearchParams({ classId: classId ?? "" })}`);

  if (bootstrapError) return <StatePanel kind="error" title="范围没有建立起来" message={bootstrapError} />;
  return <div className={ui.stackLg}>
    <div className={view.head}><div><div className={view.breadcrumbs}>班级总览 {detail && ` / ${title} / 学情详情`}</div><h2>{detail ? "班级学情详情" : "班级总览"}</h2></div>
      {(detail || comparing) && <button className={ui.button} onClick={() => setSearch(classId ? { classId } : {})}>返回班级</button>}</div>
    {comparing && result.data && comparison.data && <ClassComparison left={result.data} right={comparison.data} leftName={classNames[classId ?? ""] ?? classId ?? ""} rightName={classNames[otherClass?.class_id ?? ""] ?? otherClass?.class_id ?? ""} indicatorNames={names} />}
    {comparing && comparison.isLoading && <StatePanel kind="loading" title="正在读取对比班级" />}
    {comparing && comparison.error && <StatePanel kind="error" message={(comparison.error as Error).message} onRetry={() => comparison.refetch()} />}
    {!ready && <StatePanel kind="loading" title="正在确定数据集、运行与节点" />}
    {classesQuery.error && <StatePanel kind="error" message={(classesQuery.error as Error).message} onRetry={() => classesQuery.refetch()} />}
    {ready && !comparing && <div className={view.layout}>
      <div className={view.column}>
        <section className={view.panel}>
          <div className={view.panelHeader}><h2>{detail ? "课程观察指标" : "班级学情摘要"}</h2>
            {!detail && <div className={ui.row}><button className={ui.button} onClick={() => setSearch({ classId: classId!, view: "detail" })}>查看详情</button><button className={ui.button} disabled={!otherClass} onClick={() => setSearch({ classId: classId!, view: "compare" })}>班级对比</button></div>}</div>
          <span className={view.muted}>{title} · {nodeId}</span>
          {result.isLoading && <StatePanel kind="loading" title="正在读取班级结果" />}
          {result.error && <StatePanel kind="error" message={(result.error as Error).message} onRetry={() => result.refetch()} />}
          {result.data && (detail ? <IndicatorRadar metrics={result.data.metrics} names={names} /> : <>
            <div className={view.stats}><div className={view.stat}>名册人数<strong>{result.data.roster_count} 人</strong></div>
              <div className={view.stat}>有效观测<strong>{result.data.observed_student_count} / {result.data.roster_count} 人</strong></div></div>
            <strong>观察指标表现</strong>
            {result.data.metrics.length === 0 && <p className={view.muted}>暂无可用统计</p>}
            {result.data.metrics.map((metric) => <div className={view.barItem} key={metric.metric_id}>
              <div className={view.barTop}><span>{names[metric.scope.indicator_id ?? ""] ?? metric.name}</span><strong>{metric.unit === "ratio" ? formatRatio(metric.value) : metric.value ?? "—"}</strong></div>
              {metric.unit === "ratio" && metric.value !== null && <div className={view.track}><div className={view.fill} style={{ width: `${Math.min(100, Math.max(0, metric.value * 100))}%` }} /></div>}
              <small className={view.muted}>{metric.name} · {metric.numerator ?? "—"} / {metric.denominator ?? "—"}</small>
            </div>)}
          </>)}
        </section>
        {!detail && <section className={view.panel}><div className={view.panelHeader}><h2>需要关注</h2><button className={ui.button} onClick={() => setSearch({ classId: classId!, view: "detail" })}>查看全部</button></div>
          {(result.data?.findings ?? []).length === 0 && <span className={view.muted}>暂无诊断发现</span>}
          {result.data?.findings.map((finding) => <div className={view.finding} key={finding.finding_id}><div className={view.findingTop}>
            <strong>{names[finding.indicator_id] ?? finding.indicator_id}</strong>
            {finding.evidence_refs[0] && <button onClick={() => setEvidence(finding.evidence_refs[0])}>查看依据 ›</button>}
          </div><p>{finding.description}</p><span className={view.muted}>{finding.evidence_status === "insufficient" ? "证据不足" : finding.evidence_status === "conflicting" ? "证据冲突" : "证据充足"}</span></div>)}
        </section>}
      </div>
      {detail ? <section className={view.panel}><div className={view.panelHeader}><h2>班级问题与依据</h2></div>
        {(result.data?.findings ?? []).length === 0 && <span className={view.muted}>暂无诊断发现</span>}
        {result.data?.findings.map((finding) => <div className={view.finding} key={finding.finding_id}><div className={view.findingTop}><strong>{names[finding.indicator_id] ?? finding.indicator_id}</strong>
          {finding.evidence_refs[0] && <button onClick={() => setEvidence(finding.evidence_refs[0])}>查看依据 ›</button>}</div><p>{finding.description}</p><span className={view.muted}>{finding.evidence_status === "insufficient" ? "证据不足" : finding.evidence_status === "conflicting" ? "证据冲突" : "证据充足"}</span></div>)}</section>
      : <section className={view.panel}><div className={view.panelHeader}><h2>学生名册</h2><div className={view.tabs}>{classes.map((item) => <button key={item.class_id} aria-pressed={item.class_id === classId} className={`${view.tab} ${item.class_id === classId ? view.tabActive : ""}`} onClick={() => setClass(item.class_id)}>{classNames[item.class_id] ?? item.class_id}</button>)}</div>
        <input className={ui.input} placeholder="搜索学生编号" aria-label="按学生编号过滤名册" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
        {roster.isLoading && <StatePanel kind="loading" title="正在读取名册" />}
        {roster.error && <StatePanel kind="error" message={(roster.error as Error).message} onRetry={() => roster.refetch()} />}
        {roster.data && visible.length === 0 && <StatePanel kind="empty" title="暂无匹配的学生" />}
        <div className={view.grid}>{visible.map((student) => <button key={student.student_id} type="button" className={view.student} onClick={() => goStudent(student.student_id)} aria-label={`查看画像 ${student.student_id}`}>
          <strong>{student.student_id}</strong><span>{student.observed_event_count === 0 ? "无记录" : student.observed_event_count == null ? "记录待查询" : `观测记录 ${student.observed_event_count} 条`}</span><em>→</em></button>)}</div>
        {roster.data && <div className={`${ui.rowBetween} ${ui.small}`} style={{ marginTop: 18 }}><span className={view.muted}>本页筛选 · 共 {roster.data.total} 人</span><div className={ui.row}>
          <button className={ui.button} disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>{page}</span><button className={ui.button} disabled={page * roster.data.page_size >= roster.data.total} onClick={() => setPage(page + 1)}>下一页</button></div></div>}
      </section>}
    </div>}
    <EvidencePanel target={evidence} runId={runId} catalogVersion={result.data?.catalog_version ?? null} onClose={() => setEvidence(null)} />
  </div>;
}

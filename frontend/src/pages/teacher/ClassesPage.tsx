import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  getCatalog,
  getClassProfile,
  listAlerts,
  listClasses,
  listDatasets,
  listRuns,
  listStudents,
} from "../../api/service";
import type { ClassResultView, ClassSummary } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { nodeLabel } from "../../lib/labels";
import { distributionText } from "../../components/learning/StudentRoster";
import { ClassComparison } from "../../components/learning/ClassComparison";
import { FindingsList } from "../../components/learning/FindingsList";
import { IndicatorRadar } from "../../components/learning/IndicatorRadar";
import { MetricList } from "../../components/learning/MetricList";
import { AttentionList, StudentRoster } from "../../components/learning/StudentRoster";
import { StatePanel } from "../../components/feedback/StatePanel";
import { EvidenceDrawer } from "../../components/evidence/EvidenceDrawer";
import type { EvidenceRef } from "../../api/types";
import { performanceLabel, evidenceStatusLabel } from "../../lib/format";

interface SavedView {
  classId: string | null;
  nodeId: string | null;
  page: number;
  search: string;
  scrollY: number;
}

const SAVED_VIEW_KEY = "zz-classes-view";

function readSavedView(): SavedView | null {
  try {
    const raw = sessionStorage.getItem(SAVED_VIEW_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as SavedView;
  } catch {
    return null;
  }
}

export function ClassesPage() {
  const { scopeKey } = useSession();
  const navigate = useNavigate();
  const datasetId = scopeKey.datasetId;
  const saved = useRef<SavedView | null>(readSavedView());
  const [classId, setClassId] = useState<string | null>(saved.current?.classId ?? null);
  const [nodeId, setNodeId] = useState<string | null>(saved.current?.nodeId ?? null);
  const [page, setPage] = useState(saved.current?.page ?? 1);
  const [search, setSearch] = useState(saved.current?.search ?? "");
  const [showCompare, setShowCompare] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);

  const classesQuery = useQuery<ClassSummary[]>({
    queryKey: queryKeys.classes(scopeKey),
    queryFn: () => listClasses(datasetId as string),
    enabled: Boolean(datasetId),
  });
  const datasetsQuery = useQuery({
    queryKey: queryKeys.datasets(scopeKey),
    queryFn: listDatasets,
    enabled: Boolean(datasetId),
  });
  const runsQuery = useQuery({
    queryKey: queryKeys.runs(scopeKey),
    queryFn: () => listRuns(datasetId as string),
    enabled: Boolean(datasetId),
  });
  const catalogQuery = useQuery({
    queryKey: queryKeys.catalog(scopeKey, null),
    queryFn: () => getCatalog(),
    enabled: Boolean(datasetId),
  });
  const alertsQuery = useQuery({
    queryKey: queryKeys.alerts(scopeKey),
    queryFn: () => listAlerts(datasetId as string),
    enabled: Boolean(datasetId),
  });

  const run = runsQuery.data?.find((item) => item.status === "succeeded") ?? runsQuery.data?.[0] ?? null;
  const nodes = run?.node_ids ?? [];

  const activeClassId = classId ?? classesQuery.data?.[0]?.class_id ?? null;
  const activeNodeId = nodeId ?? nodes[0] ?? null;

  useEffect(() => {
    if (!classId && classesQuery.data?.length) setClassId(classesQuery.data[0].class_id);
  }, [classId, classesQuery.data]);

  useEffect(() => {
    if (!nodeId && nodes.length) setNodeId(nodes[0]);
  }, [nodeId, nodes]);

  const profile = useQuery<{ result: ClassResultView; freshness: string; limitations: string[] }>({
    queryKey: queryKeys.classProfile(scopeKey, activeClassId ?? "", activeNodeId, null),
    queryFn: () => getClassProfile(datasetId as string, activeClassId as string, activeNodeId),
    enabled: Boolean(datasetId && activeClassId && activeNodeId),
  });

  const roster = useQuery({
    queryKey: queryKeys.roster(scopeKey, activeClassId, page),
    queryFn: () => listStudents(datasetId as string, { classId: activeClassId, page, pageSize: 20 }),
    enabled: Boolean(datasetId && activeClassId),
  });

  useEffect(() => {
    if (saved.current?.scrollY && roster.data && profile.data) {
      window.scrollTo({ top: saved.current.scrollY });
      saved.current.scrollY = 0;
    }
  }, [roster.data, profile.data]);

  const names = useMemo(() => {
    const map: Record<string, string> = {};
    catalogQuery.data?.indicators.forEach((indicator) => {
      map[indicator.indicator_id] = indicator.description;
    });
    return map;
  }, [catalogQuery.data]);

  const order = useMemo(
    () => (catalogQuery.data?.indicators ?? []).map((indicator) => indicator.indicator_id),
    [catalogQuery.data],
  );

  const filteredStudents = useMemo(() => {
    const items = roster.data?.items ?? [];
    const text = search.trim().toLowerCase();
    return text ? items.filter((student) => student.student_id.toLowerCase().includes(text)) : items;
  }, [roster.data, search]);

  const openStudent = (studentId: string) => {
    sessionStorage.setItem(
      SAVED_VIEW_KEY,
      JSON.stringify({
        classId: activeClassId,
        nodeId: activeNodeId,
        page,
        search,
        scrollY: window.scrollY,
      } satisfies SavedView),
    );
    navigate(`/teacher/students/${encodeURIComponent(studentId)}`);
  };

  const onEvidence = (ref: EvidenceRef) => setEvidence(ref);
  const result = profile.data?.result;

  if (!datasetId) {
    return (
      <div className="stackLg">
        <div className="pageHeader">
          <h1 className="pageTitle">班级总览</h1>
        </div>
        <StatePanel
          kind="empty"
          title="当前会话还没有绑定数据集"
          message="教师需要先导入或选择一个数据集，才能读取班级学情。"
          action={<button type="button" className="btn btnPrimary" onClick={() => navigate("/teacher/data")}>去数据管理</button>}
        />
      </div>
    );
  }

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">班级总览</h1>
          <p className="pageSub">
            {datasetsQuery.data?.find((item) => item.dataset_id === datasetId)?.display_name ?? "当前数据集"}
            {activeNodeId ? ` · ${nodeLabel(activeNodeId, nodes)}` : ""}
          </p>
        </div>
        <div className="pageActions">
          <label className="field">
            <span className="fieldLabel">班级</span>
            <select
              className="select"
              value={activeClassId ?? ""}
              onChange={(event) => { setClassId(event.target.value); setPage(1); }}
            >
              {(classesQuery.data ?? []).map((item) => (
                <option key={item.class_id} value={item.class_id}>
                  {item.display_name || item.class_id}（{item.roster_count} 人）
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="fieldLabel">节点</span>
            <select className="select" value={activeNodeId ?? ""} onChange={(event) => setNodeId(event.target.value)}>
              {nodes.map((node) => <option key={node} value={node}>{nodeLabel(node, nodes)}</option>)}
            </select>
          </label>
          <button
            type="button"
            className="btn"
            aria-pressed={showCompare}
            onClick={() => setShowCompare((value) => !value)}
            disabled={!run || nodes.length === 0}
          >
            {showCompare ? "返回班级视图" : "班级比较"}
          </button>
          <button type="button" className="btn" onClick={() => { void profile.refetch(); void roster.refetch(); }}>
            刷新
          </button>
        </div>
      </div>

      {classesQuery.isPending || runsQuery.isPending ? <StatePanel kind="loading" title="正在读取班级与运行" /> : null}

      {classesQuery.isError ? (
        <StatePanel
          kind="error"
          title="班级读取失败"
          message={toErrorMessage(classesQuery.error)}
          requestId={errorRequestId(classesQuery.error)}
          onRetry={() => void classesQuery.refetch()}
        />
      ) : null}

      {!classesQuery.isPending && !classesQuery.isError && (classesQuery.data ?? []).length === 0 ? (
        <StatePanel kind="empty" title="这个数据集没有班级" message="导入的六表里没有可读的班级记录。" />
      ) : null}

      {!runsQuery.isPending && !runsQuery.isError && runsQuery.data?.length === 0 ? (
        <StatePanel
          kind="not-ready"
          title="还没有正式分析运行"
          message="数据已导入但 A→B→C 任务尚未完成，因此没有班级学情可读。"
          action={<button type="button" className="btn" onClick={() => navigate("/teacher/data?tab=jobs")}>查看任务进度</button>}
        />
      ) : null}

      {showCompare && run && activeNodeId ? (
        <section className="section" aria-labelledby="compare-title">
          <div className="sectionHead">
            <h2 className="sectionTitle" id="compare-title">班级比较</h2>
            <span className="sectionHint">可比性与差值由服务端判定；不可比时只并列原值与原因。</span>
          </div>
          <ClassComparison
            runId={run.run_id}
            nodeId={activeNodeId}
            classes={classesQuery.data ?? []}
            names={names}
            defaultLeft={activeClassId ?? ""}
            defaultRight={(classesQuery.data ?? []).find((item) => item.class_id !== activeClassId)?.class_id}
          />
        </section>
      ) : null}

      {profile.isPending && activeClassId && activeNodeId ? <StatePanel kind="loading" title="正在读取班级画像" /> : null}

      {profile.isError ? (
        <StatePanel
          kind={errorCode(profile.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
          title={errorCode(profile.error) === "RESULT_NOT_READY" ? "该班级在此节点尚无正式学情" : "班级画像读取失败"}
          message={toErrorMessage(profile.error)}
          requestId={errorRequestId(profile.error)}
          onRetry={() => void profile.refetch()}
        />
      ) : null}

      {result ? (
        <>
          <section className="section" aria-labelledby="summary-title">
            <div className="statGrid">
              <div className="stat">
                <div className="statLabel">名册人数</div>
                <div className="statValue">{result.roster_count}</div>
                <div className="statMeta">含无记录学生，作为分母使用</div>
              </div>
              <div className="stat">
                <div className="statLabel">有效观测学生</div>
                <div className="statValue">{result.observed_student_count}</div>
                <div className="statMeta">有可读记录的学生人数</div>
              </div>
              <div className="stat">
                <div className="statLabel">需关注</div>
                <div className="statValue">{result.attention_students.length}</div>
                <div className="statMeta">由服务端标记，不按得分阈值自行设定</div>
              </div>
            </div>
            {result.limitations.length ? (
              <p className="small muted" style={{ marginTop: 12 }}>限制：{result.limitations.join("；")}</p>
            ) : null}
          </section>

          <section className="section grid2" aria-label="班级指标与共性问题">
            <div>
              <div className="sectionHead">
                <h2 className="sectionTitle">观察指标</h2>
                <span className="sectionHint">有效得分率，0—100</span>
              </div>
              <IndicatorRadar metrics={result.metrics} names={names} order={order} />
              <details style={{ marginTop: 16 }}>
                <summary className="small muted" style={{ cursor: "pointer" }}>全部指标明细</summary>
                <MetricList metrics={result.metrics} names={names} onEvidence={onEvidence} />
              </details>
            </div>
            <div className="stackLg">
              <div>
                <div className="sectionHead">
                  <h2 className="sectionTitle">共性问题</h2>
                  <span className="sectionHint">{result.common_findings.length} 条</span>
                </div>
                <FindingsList findings={result.common_findings} names={names} onEvidence={onEvidence} emptyText="服务端没有返回共性问题；这不等于没有问题。" />
              </div>
              <div>
                <div className="sectionHead">
                  <h2 className="sectionTitle">表现分布</h2>
                  <span className="sectionHint">计数 / 名册人数</span>
                </div>
                {result.performance_distribution.length === 0 ? (
                  <p className="muted small">服务端没有返回表现分布。</p>
                ) : (
                  <div className="tableWrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th scope="col">观察指标</th>
                          <th scope="col">表现</th>
                          <th scope="col">证据</th>
                          <th scope="col" className="numeric">人数 / 名册</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.performance_distribution.map((bucket, index) => (
                          <tr key={`${bucket.indicator_id}-${bucket.performance}-${index}`}>
                            <td className="small">{names[bucket.indicator_id] ?? bucket.indicator_id}</td>
                            <td className="small">{performanceLabel(bucket.performance)}</td>
                            <td className="small">{evidenceStatusLabel(bucket.evidence_status)}</td>
                            <td className="numeric">{distributionText(bucket.count, bucket.roster_count)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
              <div>
                <div className="sectionHead">
                  <h2 className="sectionTitle">关注学生</h2>
                </div>
                <AttentionList items={result.attention_students} />
              </div>
            </div>
          </section>

          <section className="section" aria-labelledby="roster-title">
            <div className="sectionHead">
              <h2 className="sectionTitle" id="roster-title">名册</h2>
              <span className="sectionHint">当前页筛选只在已加载的名册数据内进行</span>
            </div>
            {roster.isPending ? <StatePanel kind="loading" title="正在读取名册" /> : null}
            {roster.isError ? (
              <StatePanel
                kind="error"
                title="名册读取失败"
                message={toErrorMessage(roster.error)}
                requestId={errorRequestId(roster.error)}
                onRetry={() => void roster.refetch()}
              />
            ) : null}
            {roster.data ? (
              <StudentRoster
                students={filteredStudents}
                total={roster.data.total}
                page={roster.data.page}
                pageSize={roster.data.page_size}
                attention={result.attention_students}
                onPage={setPage}
                onOpenStudent={openStudent}
                search={search}
                onSearch={setSearch}
              />
            ) : null}
            {alertsQuery.data?.length ? (
              <p className="small muted" style={{ marginTop: 12 }}>
                顶部提醒还有 {alertsQuery.data.length} 条待处理；提醒已读不等于已处理。
              </p>
            ) : null}
          </section>
        </>
      ) : null}

      <EvidenceDrawer
        refItem={evidence}
        context={{ class_id: activeClassId, student_id: null }}
        onClose={() => setEvidence(null)}
      />
    </div>
  );
}

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  getAnswerRecords,
  getCatalog,
  getPlan,
  getProgress,
  getStudentProfile,
} from "../../api/service";
import type { AnswerRecordListItem, EvidenceRef, PlanView, StudentResultView } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { EvidenceDrawer } from "../../components/evidence/EvidenceDrawer";
import { StatePanel } from "../../components/feedback/StatePanel";
import { FindingsList } from "../../components/learning/FindingsList";
import { IndicatorRadar } from "../../components/learning/IndicatorRadar";
import { MetricList } from "../../components/learning/MetricList";
import { ProgressView } from "../../components/learning/ProgressView";
import { TaskGroupView } from "../../components/learning/TaskGroupView";
import { PlanEditor } from "../../components/learning/PlanEditor";
import { formatDate, formatScore, gradingStatusLabel } from "../../lib/format";
import { IconArrowLeft } from "../../components/icons";

const TABS = [
  { key: "overview", label: "概览" },
  { key: "progress", label: "进展" },
  { key: "records", label: "作答记录" },
  { key: "plan", label: "学习方案" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function StudentProfilePage() {
  const { studentId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { scopeKey } = useSession();
  const datasetId = scopeKey.datasetId;
  const tab = (searchParams.get("tab") as TabKey | null) ?? "overview";
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);
  const [recordsPage, setRecordsPage] = useState(1);
  const [editorOpen, setEditorOpen] = useState(false);

  const savedView = useMemo(() => {
    try {
      const raw = sessionStorage.getItem("zz-classes-view");
      return raw ? (JSON.parse(raw) as { classId: string | null; nodeId: string | null; page: number; search: string }) : null;
    } catch {
      return null;
    }
  }, []);

  const nodeId = searchParams.get("nodeId") ?? savedView?.nodeId ?? null;

  const profile = useQuery<{ result: StudentResultView; freshness: string; limitations: string[] }>({
    queryKey: queryKeys.studentProfile(scopeKey, studentId, nodeId, null),
    queryFn: () => getStudentProfile(datasetId as string, studentId, { nodeId }),
    enabled: Boolean(datasetId && studentId),
  });

  const catalog = useQuery({
    queryKey: queryKeys.catalog(scopeKey, profile.data?.result.catalog_version ?? null),
    queryFn: () => getCatalog(profile.data?.result.catalog_version ?? null),
    enabled: Boolean(datasetId),
  });

  const plan = useQuery<PlanView>({
    queryKey: queryKeys.plan(scopeKey, studentId, nodeId, null),
    queryFn: () => getPlan(datasetId as string, studentId, { nodeId }),
    enabled: Boolean(datasetId && studentId),
  });

  const progress = useQuery({
    queryKey: queryKeys.progress(scopeKey, studentId, 1),
    queryFn: () => getProgress(datasetId as string, studentId, { page: 1, pageSize: 50 }),
    enabled: Boolean(datasetId && studentId && tab === "progress"),
  });

  const result = profile.data?.result;
  const records = useQuery({
    queryKey: queryKeys.answerRecords(scopeKey, result?.run_id ?? "", studentId, result?.node_id ?? nodeId ?? "", recordsPage),
    queryFn: () => getAnswerRecords(result!.run_id, studentId, { nodeId: result!.node_id, page: recordsPage, pageSize: 20 }),
    enabled: Boolean(tab === "records" && result?.run_id && result?.node_id),
  });

  const names = useMemo(() => {
    const map: Record<string, string> = {};
    catalog.data?.indicators.forEach((indicator) => { map[indicator.indicator_id] = indicator.description; });
    return map;
  }, [catalog.data]);
  const order = useMemo(() => (catalog.data?.indicators ?? []).map((indicator) => indicator.indicator_id), [catalog.data]);
  const materialNames = useMemo(() => {
    const map: Record<string, string> = {};
    (catalog.data?.materials ?? []).forEach((material) => {
      map[material.material_id] = material.source_label || material.content.slice(0, 18);
    });
    return map;
  }, [catalog.data]);

  const backToClass = () => {
    const query = new URLSearchParams();
    if (savedView?.classId) query.set("classId", savedView.classId);
    if (savedView?.nodeId) query.set("nodeId", savedView.nodeId);
    if (savedView?.page) query.set("page", String(savedView.page));
    navigate(`/teacher/classes${query.toString() ? `?${query.toString()}` : ""}`);
  };

  if (!datasetId) {
    return (
      <StatePanel
        kind="empty"
        title="当前会话没有绑定数据集"
        message="请先在数据管理里选择数据集。"
        action={<Link className="btn" to="/teacher/data">去数据管理</Link>}
      />
    );
  }

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <div className="row" style={{ gap: 12 }}>
            <button type="button" className="btn btnSm" onClick={backToClass}>
              <IconArrowLeft size={16} />
              返回班级
            </button>
            <h1 className="pageTitle">学生画像 · {studentId}</h1>
          </div>
          <p className="pageSub">
            {result ? `班级 ${result.class_id} · 当前观察节点 · 证据与版本随记录保留` : "正在读取正式学情"}
          </p>
        </div>
        <div className="pageActions">
          <button
            type="button"
            className="btn btnPrimary"
            onClick={() => navigate(`/teacher/chat?student=${encodeURIComponent(studentId)}&node=${encodeURIComponent(result?.node_id ?? nodeId ?? "")}`)}
          >
            就此学生提问
          </button>
        </div>
      </div>

      {profile.isPending ? <StatePanel kind="loading" title="正在读取学生画像" /> : null}
      {profile.isError ? (
        <StatePanel
          kind={errorCode(profile.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
          title={errorCode(profile.error) === "RESULT_NOT_READY" ? "该学生在此节点尚无正式学情" : "学生画像读取失败"}
          message={toErrorMessage(profile.error)}
          requestId={errorRequestId(profile.error)}
          onRetry={() => void profile.refetch()}
        />
      ) : null}

      {result ? (
        <>
          <div className="tabs" role="tablist" aria-label="学生画像页签">
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={tab === item.key}
                className={tab === item.key ? "tab tabActive" : "tab"}
                onClick={() => {
                  const next = new URLSearchParams(searchParams);
                  next.set("tab", item.key);
                  setSearchParams(next);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          {tab === "overview" ? (
            <section className="section" aria-label="学情概览">
              <div className="grid2">
                <div>
                  <div className="sectionHead">
                    <h2 className="sectionTitle">观察指标</h2>
                    <span className="sectionHint">有效得分率 0—100</span>
                  </div>
                  <IndicatorRadar metrics={result.metrics} names={names} order={order} />
                  <details style={{ marginTop: 16 }}>
                    <summary className="small muted" style={{ cursor: "pointer" }}>全部指标明细</summary>
                    <MetricList metrics={result.metrics} names={names} onEvidence={setEvidence} />
                  </details>
                </div>
                <div>
                  <div className="sectionHead">
                    <h2 className="sectionTitle">诊断发现</h2>
                    <span className="sectionHint">{result.findings.length} 条 · 证据与表现分开判断</span>
                  </div>
                  <FindingsList findings={result.findings} names={names} onEvidence={setEvidence} />
                  {result.summary ? <p className="prose" style={{ marginTop: 12 }}>{result.summary}</p> : null}
                  {result.strategy_signals.length ? (
                    <div style={{ marginTop: 12 }}>
                      <h3 className="paneTitle">策略信号</h3>
                      <ul>
                        {result.strategy_signals.map((signal) => (
                          <li key={signal.signal_id} className="small">
                            {names[signal.indicator_id] ?? signal.indicator_id} · {signal.signal_type}
                            {signal.limitations.length ? `（限制：${signal.limitations.join("；")}）` : ""}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {result.limitations.length ? <p className="small muted">限制：{result.limitations.join("；")}</p> : null}
                </div>
              </div>

              <div className="section">
                <div className="sectionHead">
                  <h2 className="sectionTitle">学习方案摘要</h2>
                  <span className="sectionHint">正式版本以服务端为准</span>
                </div>
                {plan.isPending ? <StatePanel kind="loading" title="正在读取学习方案" /> : null}
                {plan.isError ? (
                  <StatePanel
                    kind={errorCode(plan.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
                    title={errorCode(plan.error) === "RESULT_NOT_READY" ? "该学生尚无正式学习方案" : "学习方案读取失败"}
                    message={toErrorMessage(plan.error)}
                  />
                ) : null}
                {plan.data ? (
                  <PlanSummary
                    plan={plan.data}
                    names={names}
                  />
                ) : null}
                <div className="row" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setEditorOpen(true)}
                    disabled={!plan.data}
                    title={plan.data ? "打开学习方案编辑器" : "尚未读取到正式方案"}
                  >
                    编辑学习方案
                  </button>
                  <span className="small muted">
                    {plan.data ? "改目标、说明与活动，检查并发布后生成新的正式版本。" : "尚未读取到正式方案，不能编辑。"}
                  </span>
                </div>
              </div>
            </section>
          ) : null}

          {tab === "progress" ? (
            <section className="section" aria-label="学习进展">
              <div className="sectionHead">
                <h2 className="sectionTitle">学习进展</h2>
                <span className="sectionHint">分类与可比性来自 B，不由前端按差值推断</span>
              </div>
              {progress.isPending ? <StatePanel kind="loading" title="正在读取进展" /> : null}
              {progress.isError ? (
                <StatePanel kind="error" title="进展读取失败" message={toErrorMessage(progress.error)} requestId={errorRequestId(progress.error)} />
              ) : null}
              {progress.data ? (
                <ProgressView items={progress.data.items} names={names} onEvidence={setEvidence} />
              ) : null}
            </section>
          ) : null}

          {tab === "records" ? (
            <section className="section" aria-label="作答记录">
              <div className="sectionHead">
                <h2 className="sectionTitle">作答记录</h2>
                <span className="sectionHint">点击“查看依据”走统一证据接口，保留精确版本</span>
              </div>
              {records.isPending ? <StatePanel kind="loading" title="正在读取作答记录" /> : null}
              {records.isError ? (
                <StatePanel kind="error" title="作答记录读取失败" message={toErrorMessage(records.error)} requestId={errorRequestId(records.error)} onRetry={() => void records.refetch()} />
              ) : null}
              {records.data ? (
                <>
                  <AnswerRecords items={records.data.items} onEvidence={setEvidence} materialNames={materialNames} />
                  <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
                    <span className="small muted">共 {records.data.total} 条</span>
                    <button type="button" className="btn btnSm" onClick={() => setRecordsPage((value) => Math.max(1, value - 1))} disabled={recordsPage <= 1}>上一页</button>
                    <button
                      type="button"
                      className="btn btnSm"
                      onClick={() => setRecordsPage((value) => value + 1)}
                      disabled={recordsPage * records.data.page_size >= records.data.total}
                    >
                      下一页
                    </button>
                  </div>
                </>
              ) : null}
            </section>
          ) : null}

          {tab === "plan" ? (
            <section className="section" aria-label="学习方案">
              {plan.isPending ? <StatePanel kind="loading" title="正在读取学习方案" /> : null}
              {plan.isError ? (
                <StatePanel
                  kind={errorCode(plan.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
                  title={errorCode(plan.error) === "RESULT_NOT_READY" ? "该学生尚无正式学习方案" : "学习方案读取失败"}
                  message={toErrorMessage(plan.error)}
                  requestId={errorRequestId(plan.error)}
                />
              ) : null}
              {plan.data ? (
                <div className="stackLg">
                  <PlanSummary plan={plan.data} names={names} />
                  <TaskGroupView
                    groups={plan.data.plan.task_groups}
                    activities={plan.data.plan.recommendations.flatMap((item) => item.activities)}
                    names={names}
                    mode="teacher"
                  />
                  <div>
                    <h3 className="paneTitle">调整条件</h3>
                    <ul>
                      {plan.data.plan.recommendations.flatMap((item) => item.adjustment_conditions).map((condition, index) => (
                        <li key={`${condition.observable_condition}-${index}`} className="small">
                          {condition.observable_condition} → {condition.next_action}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}

      <EvidenceDrawer
        refItem={evidence}
        context={{ student_id: studentId, class_id: result?.class_id ?? null }}
        onClose={() => setEvidence(null)}
      />
      <PlanEditor
        open={editorOpen}
        studentId={studentId}
        nodeId={result?.node_id ?? nodeId ?? plan.data?.plan.node_id ?? ""}
        onClose={() => setEditorOpen(false)}
        onPublished={() => {
          void plan.refetch();
          void profile.refetch();
        }}
      />
    </div>
  );
}

function PlanSummary({ plan, names }: { plan: PlanView; names: Record<string, string> }) {
  const planData = plan.plan;
  return (
    <div className="stack">
      <div className="planHead">
        <div>
          <h3 className="planGoal">{planData.recommendations[0]?.goal ?? "方案没有目标文本"}</h3>
          <p className="small muted" style={{ marginTop: 4 }}>
            {planData.created_at ? `发布于 ${formatDate(planData.created_at)}` : "正式发布版本"} · {planData.publication_status === "published" ? "已发布" : "发布状态待确认"}
          </p>
          <p className="planMeta">
            <span>数据新鲜度 {plan.freshness === "current" ? "当前" : "历史"}</span>
            <span>{planData.task_groups.length} 个任务组</span>
            <span>{planData.recommendations.length} 条建议</span>
          </p>
        </div>
        <span className="tag">{planData.recommendations.length} 条建议</span>
      </div>
      {planData.recommendations[0]?.student_intro ? (
        <p className="prose">{planData.recommendations[0].student_intro}</p>
      ) : null}
      {plan.applicability ? (
        <div className="statePanel">
          <span className="stateTitle">适用性复查：{plan.applicability.result === "applicable" ? "安排仍然适用" : "需要重新安排"}</span>
          <span className="small muted">复查时间 {formatDate(plan.applicability.checked_at)}</span>
          {plan.applicability.limitations.length ? (
            <span className="small">限制：{plan.applicability.limitations.join("；")}</span>
          ) : null}
        </div>
      ) : (
        <p className="small muted">没有返回值级适用性复查记录。</p>
      )}
      {plan.limitations.length ? <p className="small muted">限制：{plan.limitations.join("；")}</p> : null}
      {planData.task_groups.map((group) => (
        <p className="small" key={group.group_id}>
          任务组 {group.order}：{group.goal}（{group.required_activity_ids.length} 项必需）
          {group.goal_target_key ? ` · 指标 ${names[group.goal_target_key] ?? group.goal_target_key}` : ""}
        </p>
      ))}
    </div>
  );
}

function AnswerRecords({
  items,
  onEvidence,
  materialNames,
}: {
  items: AnswerRecordListItem[];
  onEvidence: (ref: EvidenceRef) => void;
  materialNames: Record<string, string>;
}) {
  if (items.length === 0) {
    return <StatePanel kind="empty" title="这个节点还没有作答记录" message="没有记录不等于 0 分，也不等于未掌握。" />;
  }
  return (
    <div className="tableWrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">来源</th>
            <th scope="col">材料</th>
            <th scope="col">状态</th>
            <th scope="col" className="numeric">得分 / 满分</th>
            <th scope="col">发生时间</th>
            <th scope="col">依据</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.record_key}>
              <td className="small">{item.origin === "official" ? "正式记录" : "在线作答"}</td>
              <td className="small">{item.material_id ? materialNames[item.material_id] ?? "题目记录" : "—"}</td>
              <td className="small">{gradingStatusLabel(item.grading_status)}</td>
              <td className="numeric">{formatScore(item.score, item.max_score)}</td>
              <td className="small">{formatDate(item.occurred_at)}</td>
              <td>
                <button type="button" className="btn btnSm btnGhost" onClick={() => onEvidence(item.evidence_ref)}>
                  查看依据
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

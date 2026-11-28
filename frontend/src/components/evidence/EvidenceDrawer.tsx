import { useCallback, useEffect, useRef, useState } from "react";
import { errorCode, errorRequestId, isAbortError, toErrorMessage } from "../../api/client";
import { resolveEvidence } from "../../api/service";
import type { EvidenceDetail, EvidenceRef, MaterialView, Metric } from "../../api/types";
import { formatDate, formatPercent, formatScore } from "../../lib/format";
import { metricNameLabel, unitLabel } from "../../lib/labels";
import { IconArrowLeft, IconClose } from "../icons";
import { StatePanel } from "../feedback/StatePanel";
import { evidenceRefKey, type EvidenceContext } from "./types";

interface Props {
  refItem: EvidenceRef | null;
  context?: EvidenceContext;
  onClose: () => void;
}

const KIND_LABELS: Record<string, string> = {
  record: "作答记录",
  metric: "指标",
  curriculum: "课程知识",
  derived: "诊断/建议",
  learning_event: "学习事件",
  attempt: "在线作答",
  finding: "诊断发现",
  progress: "学习进展",
  material: "学习材料",
  grade: "评分",
  evidence: "证据",
};

export function evidenceKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

const str = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);
const num = (value: unknown): number | null => (typeof value === "number" ? value : null);
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

function refVersion(ref: EvidenceRef): string {
  const entries: Array<[string, string | null | undefined]> = [
    ["运行", ref.run_id],
    ["分析版本", ref.analysis_version],
    ["课程目录", ref.catalog_version],
    ["知识版本", ref.knowledge_version],
    ["方案版本", ref.plan_version],
  ];
  const parts = entries.filter(([, value]) => Boolean(value)).map(([label, value]) => `${label} ${value}`);
  return parts.length ? parts.join(" · ") : "未附版本";
}

/** 证据抽屉：按 detail_type 渲染服务端投影，不展示整份 JSON。 */
export function EvidenceDrawer({ refItem, context, onClose }: Props) {
  const [stack, setStack] = useState<EvidenceRef[]>(refItem ? [refItem] : []);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const current = stack[stack.length - 1] ?? null;

  useEffect(() => {
    setStack(refItem ? [refItem] : []);
  }, [refItem]);

  useEffect(() => {
    if (!current) {
      setDetail(null);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    resolveEvidence(current, context, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setDetail(result);
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) return;
        setError(reason);
        setDetail(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [current, context]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      abortRef.current?.abort();
    };
  }, [onClose]);

  const push = useCallback(
    (ref: EvidenceRef) => {
      setStack((previous) => {
        if (previous.some((item) => evidenceRefKey(item) === evidenceRefKey(ref))) return previous;
        return [...previous, ref];
      });
    },
    [],
  );

  if (!current) return null;

  const metricDetail = detail?.detail_type === "metric" ? ((detail.detail.metric ?? null) as Metric | null) : null;
  const displayTitle = metricDetail ? metricNameLabel(metricDetail.name) : detail?.title ?? "证据详情";

  return (
    <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="证据详情" onMouseDown={(event) => event.stopPropagation()}>
        <div className="drawerHead">
          <div>
            <h2 className="drawerTitle">{displayTitle}</h2>
            <p className="xs muted">{evidenceKindLabel(current.kind)} · 按提交时的版本读取</p>
            <details>
              <summary className="xs muted" style={{ cursor: "pointer" }}>版本与定位信息</summary>
              <p className="xs muted breakAnywhere" style={{ marginTop: 4 }}>
                定位：{current.kind} / {current.id}
                <br />
                {refVersion(current)}
              </p>
            </details>
          </div>
          <button type="button" className="iconBtn" onClick={onClose} aria-label="关闭证据抽屉">
            <IconClose size={18} />
          </button>
        </div>

        <div className="drawerBody">
          {loading ? <StatePanel kind="loading" title="正在读取证据" /> : null}
          {error ? (
            <StatePanel
              kind={errorCode(error) === "NOT_FOUND" ? "expired" : "error"}
              title={errorCode(error) === "NOT_FOUND" ? "证据不存在或已过期" : "证据读取失败"}
              message={toErrorMessage(error)}
              requestId={errorRequestId(error)}
            />
          ) : null}
          {!loading && !error && detail ? <EvidenceBody detail={detail} onOpenRelated={push} /> : null}
        </div>

        <div className="drawerFoot">
          <button
            type="button"
            className="btn btnSm"
            onClick={() => setStack((previous) => previous.slice(0, -1))}
            disabled={stack.length <= 1}
          >
            <IconArrowLeft size={16} />
            返回上一处
          </button>
          <span className="xs muted">第 {stack.length} 层引用</span>
          <button type="button" className="btn btnSm" onClick={onClose}>关闭</button>
        </div>
      </aside>
    </div>
  );
}

function EvidenceBody({ detail, onOpenRelated }: { detail: EvidenceDetail; onOpenRelated: (ref: EvidenceRef) => void }) {
  const data = detail.detail ?? {};
  return (
    <>
      {detail.visibility === "redacted" ? (
        <p className="small muted">这部分内容按权限做了裁剪，只显示服务端允许的投影。</p>
      ) : null}
      {detail.summary && detail.detail_type !== "metric" ? <p className="prose">{detail.summary}</p> : null}
      {detail.detail_type === "record" ? <RecordDetail data={data} /> : null}
      {detail.detail_type === "metric" ? <MetricDetail data={data} /> : null}
      {detail.detail_type === "curriculum" ? <CurriculumDetail data={data} /> : null}
      {detail.detail_type === "derived" ? <DerivedDetail data={data} /> : null}
      {detail.detail_type === "learning" ? <LearningDetail data={data} /> : null}
      {detail.limitations.length ? (
        <div>
          <h3 className="paneTitle">限制</h3>
          <ul>{detail.limitations.map((item) => <li key={item} className="small">{item}</li>)}</ul>
        </div>
      ) : null}
      {detail.related_refs.length ? (
        <div>
          <h3 className="paneTitle">相关引用</h3>
          <div className="refList">
            {detail.related_refs.map((ref, index) => (
              <button
                key={evidenceRefKey(ref)}
                type="button"
                className="btn btnSm"
                onClick={() => onOpenRelated(ref)}
              >
                {evidenceKindLabel(ref.kind)} · 第 {index + 1} 条引用
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

function RecordDetail({ data }: { data: Record<string, unknown> }) {
  const question = (data.question ?? null) as MaterialView | null;
  const rubric = Array.isArray(data.rubric_scores) ? (data.rubric_scores as Array<Record<string, unknown>>) : [];
  const quality = Array.isArray(data.quality_issues) ? (data.quality_issues as Array<Record<string, unknown>>) : [];
  return (
    <div className="stack">
      {question ? (
        <div>
          <h3 className="paneTitle">原题</h3>
          <p className="prose" style={{ whiteSpace: "pre-wrap" }}>{question.content}</p>
          {question.options?.length ? (
            <ol className="small" style={{ marginTop: 8 }}>
              {question.options.map((option) => <li key={option}>{option}</li>)}
            </ol>
          ) : null}
        </div>
      ) : null}
      <dl className="kv">
        <dt>状态</dt>
        <dd>{str(data.status) ?? "—"}</dd>
        <dt>得分</dt>
        <dd className="tabular">{formatScore(num(data.score), num(data.max_score))}</dd>
        <dt>提交时间</dt>
        <dd>{formatDate(str(data.submitted_at))}</dd>
      </dl>
      <div>
        <h3 className="paneTitle">实际作答</h3>
        <p className="prose answerLocked">{str(data.final_answer) ?? "未填写最终答案"}</p>
        {str(data.solution_steps) ? <p className="prose answerLocked small muted">{str(data.solution_steps)}</p> : null}
      </div>
      {rubric.length ? (
        <div>
          <h3 className="paneTitle">评分点</h3>
          <ul>{rubric.map((item, index) => (
            <li key={String(item.point_id ?? index)} className="small">
              {str(item.description) ?? String(item.point_id ?? "评分点")}：
              {formatScore(num(item.score), num(item.max_score))}
            </li>
          ))}</ul>
        </div>
      ) : null}
      {quality.length ? (
        <div>
          <h3 className="paneTitle">数据质量</h3>
          <ul>{quality.map((item, index) => (
            <li key={String(item.issue_id ?? index)} className="small">{str(item.message) ?? "质量提示"}</li>
          ))}</ul>
        </div>
      ) : null}
    </div>
  );
}

function readableCalculation(text: string): string {
  return text
    .replace(/\(ratio\)/gi, "（比例）")
    .replace(/\(score\)/gi, "（得分）")
    .replace(/\(count\)/gi, "（计数）");
}

function MetricDetail({ data }: { data: Record<string, unknown> }) {
  const metric = (data.metric ?? null) as Metric | null;
  const records = Array.isArray(data.record_refs) ? (data.record_refs as EvidenceRef[]) : [];
  return (
    <div className="stack">
      {metric ? (
        <dl className="kv">
          <dt>指标</dt>
          <dd>{metricNameLabel(metric.name)}（{unitLabel(metric.unit)}）</dd>
          <dt>数值</dt>
          <dd className="tabular">{metric.unit === "ratio" ? formatPercent(metric.value) : metric.value ?? "无可用结果"}</dd>
          <dt>分子 / 分母</dt>
          <dd className="tabular">{metric.numerator ?? "—"} / {metric.denominator ?? "—"}</dd>
          <dt>排除记录</dt>
          <dd className="tabular">{data.excluded_count === undefined ? metric.excluded_event_count : String(data.excluded_count)}</dd>
        </dl>
      ) : null}
      {str(data.calculation_description) ? <p className="prose">{readableCalculation(str(data.calculation_description) as string)}</p> : null}
      <p className="small muted">本次证据数量：{String(data.evidence_count ?? records.length)}</p>
      {metric?.limitations.length ? <ul>{metric.limitations.map((item) => <li key={item} className="small">{item}</li>)}</ul> : null}
    </div>
  );
}

function CurriculumDetail({ data }: { data: Record<string, unknown> }) {
  const locators = Array.isArray(data.source_locators) ? (data.source_locators as Array<Record<string, unknown>>) : [];
  return (
    <div className="stack">
      <p className="prose">{str(data.description) ?? "无定义文本"}</p>
      {str(data.snippet) ? <blockquote className="prose small">{str(data.snippet)}</blockquote> : null}
      {str(data.basis) ? <p className="small muted">依据：{str(data.basis)}</p> : null}
      {locators.length ? (
        <div>
          <h3 className="paneTitle">出处</h3>
          <ul className="sourceList">
            {locators.map((locator, index) => (
              <li key={index}>
                {str(locator.source_file) ?? "未知来源"}
                {locator.pdf_page ? ` · PDF 第 ${String(locator.pdf_page)} 页` : ""}
                {locator.printed_page ? ` · 正文第 ${String(locator.printed_page)} 页` : ""}
                {` · ${str(locator.verification_status) ?? "未核验"}`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function DerivedDetail({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="stack">
      <p className="prose">{str(data.summary) ?? "—"}</p>
      {str(data.version_label) ? <p className="small muted">版本：{str(data.version_label)}</p> : null}
      {strList(data.limitations).length ? (
        <ul>{strList(data.limitations).map((item) => <li key={item} className="small">{item}</li>)}</ul>
      ) : null}
    </div>
  );
}

function LearningDetail({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="kv">
      <dt>事件</dt>
      <dd>{str(data.event_type) ?? "—"}</dd>
      <dt>发生时间</dt>
      <dd>{formatDate(str(data.occurred_at))}</dd>
      <dt>摘要</dt>
      <dd>{str(data.summary) ?? "—"}</dd>
      <dt>得分</dt>
      <dd className="tabular">{formatScore(num(data.score), num(data.max_score))}</dd>
    </dl>
  );
}

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { EvidenceKind, EvidenceRecord, Material, Metric } from "../../api/types";
import { getMaterial, readEvidence } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useSession } from "../../app/session";
import { formatDateTime, formatEvidenceLabel, formatMetricName, formatRatio, gradingStatusLabel } from "../../lib/format";
import { StatePanel } from "../feedback/StatePanel";
import styles from "../../styles/ui.module.css";

export interface EvidenceTarget {
  kind: EvidenceKind;
  id: string;
}

interface Props {
  target: EvidenceTarget | null;
  runId: string | null;
  catalogVersion: string | null;
  onClose: () => void;
}

const titleOf: Record<EvidenceKind, string> = {
  record: "作答记录",
  metric: "统计项详情",
  material: "材料",
  curriculum: "课程条目",
};

function isRecord(value: unknown): value is EvidenceRecord {
  return Boolean(value && typeof value === "object" && "record_id" in value);
}

function isMetric(value: unknown): value is Metric {
  return Boolean(value && typeof value === "object" && "metric_id" in value);
}

function isMaterial(value: unknown): value is Material {
  return Boolean(value && typeof value === "object" && "material_id" in value);
}

/**
 * 证据面板：所有页面共用同一个实例，内容按引用种类加载，未确认字段显示空态。
 */
export function EvidencePanel(props: Props) {
  if (!props.target) return null;
  return <OpenEvidencePanel {...props} initial={props.target} />;
}

function OpenEvidencePanel({ initial, runId, catalogVersion, onClose }: Props & { initial: EvidenceTarget }) {
  const { session } = useSession();
  const dialog = useRef<HTMLDialogElement>(null);
  const [history, setHistory] = useState<EvidenceTarget[]>([]);
  const target = history.at(-1) ?? initial;

  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  const open = (next: EvidenceTarget) => setHistory((items) => [...items, next]);

  const isRunnable = target?.kind === "record" || target?.kind === "metric";
  const evidence = useQuery({
    queryKey: target.kind === "metric"
      ? queryKeys.metric(session, runId, target.id)
      : queryKeys.record(session, runId, target.id),
    queryFn: () => readEvidence(runId as string, target?.kind as "record" | "metric", target?.id as string),
    enabled: Boolean(target && isRunnable && runId),
    retry: false,
  });

  const record = isRunnable && isRecord(evidence.data) ? evidence.data : null;
  const materialId = target.kind === "material" ? target.id : record?.material_id;
  const material = useQuery({
    queryKey: [...queryKeys.material(catalogVersion, materialId ?? "none"), session?.session_id],
    queryFn: () => getMaterial(materialId as string, catalogVersion, session?.role),
    enabled: Boolean(materialId && catalogVersion && session),
    retry: false,
  });

  const loading = isRunnable ? evidence.isLoading : material.isLoading;
  const error = isRunnable ? evidence.error : material.error;
  const materialDetail = isMaterial(material.data) ? material.data : null;

  return (
    <dialog ref={dialog} className={styles.evidenceDialog} aria-label={titleOf[target.kind]}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className={styles.drawer}>
        <div className={styles.drawerHead}>
          <div>
            <span className={styles.cardTitle}>{titleOf[target.kind]}</span>
            <details className={`${styles.small} ${styles.muted}`}>
              <summary>查看来源编号</summary>
              <div>{target.id}</div>
            </details>
          </div>
          <button
            type="button"
            className={styles.button}
            onClick={onClose}
            aria-label="关闭证据面板"
          >
            关闭
          </button>
        </div>

        <div className={styles.drawerBody}>
          {history.length > 0 && (
            <button type="button" className={styles.button} onClick={() => setHistory((items) => items.slice(0, -1))}>
              返回上一条证据
            </button>
          )}
          {loading ? <StatePanel kind="loading" title="正在读取证据" /> : null}

          {error ? (
            <StatePanel
              kind="error"
              title="这条证据没有读到"
              message={(error as Error).message}
              onRetry={() => {
                if (isRunnable) void evidence.refetch();
                else void material.refetch();
              }}
            />
          ) : null}

          {!loading && !error && evidence.data && isRecord(evidence.data) ? (
            <div className={styles.stackLg}>
              <section>
                <strong>原题</strong>
                {materialDetail ? <><p>{materialDetail.content}</p>
                  {materialDetail.options?.map((option) => <div key={option}>{option}</div>)}
                </> : <p>{material.isLoading ? "正在读取原题…" : "暂无可用题面"}</p>}
              </section>
              <RecordDetail record={evidence.data} material={materialDetail} />
            </div>
          ) : null}

          {!loading && !error && evidence.data && isMetric(evidence.data) ? (
            <MetricDetail metric={evidence.data} onOpen={open} />
          ) : null}

          {target.kind === "material" && !loading && !error && materialDetail ? (
            <MaterialDetail material={materialDetail} />
          ) : null}

          {target.kind === "curriculum" ? (
            <StatePanel
              kind="info"
              title="课程条目"
              message="打开课程体系查看学习要求和出处。"
              action={<Link onClick={onClose} to={`/teacher/curriculum?${new URLSearchParams({ indicatorId: target.id })}`}>查看课程要求</Link>}
            />
          ) : null}
        </div>
      </div>
    </dialog>
  );
}

function RecordDetail({ record, material }: { record: EvidenceRecord; material: Material | null }) {
  const disposition =
    record.disposition === "quarantined"
      ? "已隔离，不计入统计"
      : record.disposition === "history"
        ? "历史版本，不重复计分"
        : "当前选中版本";

  return (
    <div className={styles.stackLg}>
      <dl className={styles.kv}>
        <dt>学生</dt>
        <dd>{record.student_id}</dd>
        <dt>材料</dt>
        <dd>{record.material_id}</dd>
        <dt>评分状态</dt>
        <dd>{gradingStatusLabel[record.grading_status]}</dd>
        <dt>得分</dt>
        <dd className={styles.num}>
          {record.score === null ? "无" : `${record.score} / ${record.max_score}`}
        </dd>
        <dt>提交时间</dt>
        <dd className={styles.num}>{formatDateTime(record.submitted_at)}</dd>
        <dt>处置</dt>
        <dd>{disposition}</dd>
      </dl>

      <div>
        <span className={styles.label}>最终作答</span>
        <p>{record.final_answer ?? "未作答／无作答内容"}</p>
      </div>

      <div>
        <span className={styles.label}>解答过程</span>
        {record.solution_steps && record.solution_steps.length > 0 ? (
          <ol className={styles.steps}>
            {record.solution_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        ) : (
          <p className={styles.muted}>暂无解答过程</p>
        )}
      </div>

      <div>
        <span className={styles.label}>评分点</span>
        {record.rubric_scores && record.rubric_scores.length > 0 ? (
          <ul className={styles.steps}>
            {record.rubric_scores.map((point) => (
              <li key={point.point_id}>
                {material?.rubric_points?.find((item) => item.point_id === point.point_id)?.description ?? point.point_id}：{point.score} 分
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.muted}>该记录没有评分点信息</p>
        )}
      </div>

      {record.disposition_note ? (
        <p className={`${styles.small} ${styles.muted}`}>{record.disposition_note}</p>
      ) : null}
    </div>
  );
}

function MetricDetail({ metric, onOpen }: { metric: Metric; onOpen: (target: EvidenceTarget) => void }) {
  return (
    <div className={styles.stackLg}>
      <dl className={styles.kv}>
        <dt>名称</dt>
        <dd>{formatMetricName(metric.name)}</dd>
        <dt>数值</dt>
        <dd className={styles.num}>
          {metric.unit === "ratio" ? formatRatio(metric.value) : (metric.value ?? "无可用结果")}
        </dd>
        <dt>分子</dt>
        <dd className={styles.num}>{metric.numerator ?? "—"}</dd>
        <dt>分母</dt>
        <dd className={styles.num}>{metric.denominator ?? "—"}</dd>
        <dt>范围</dt>
        <dd>
          {metric.scope.student_id ?? metric.scope.class_id ?? "—"} · {metric.scope.node_id}
        </dd>
        <dt>排除记录</dt>
        <dd className={styles.num}>{metric.excluded_event_count}</dd>
      </dl>

      <div>
        <span className={styles.label}>限制</span>
        <ul className={styles.steps}>
          {metric.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </div>

      <div>
        <span className={styles.label}>相关记录</span>
        <ul className={styles.steps}>
          {metric.evidence_refs.map((ref, index) => (
            <li key={`${ref.kind}-${ref.id}`}>
              <button type="button" className={styles.button} onClick={() => onOpen(ref)}>
                {formatEvidenceLabel(ref, index + 1)}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function MaterialDetail({ material }: { material: Material }) {
  const locator = material.source_locator;
  return (
    <div className={styles.stackLg}>
      <dl className={styles.kv}>
        <dt>类型</dt>
        <dd>
          {material.material_type === "question"
            ? "题目"
            : material.material_type === "card"
              ? "学习卡"
              : "教材条目"}
        </dd>
        <dt>来源文件</dt>
        <dd>{locator.source_file}</dd>
        <dt>文件页</dt>
        <dd className={styles.num}>{locator.pdf_page ?? "未提供"}</dd>
        <dt>正文页</dt>
        <dd className={styles.num}>{locator.printed_page ?? "未提供"}</dd>
        <dt>页码状态</dt>
        <dd>{locator.verification_status === "verified" ? "已核对" : "未核对"}</dd>
      </dl>

      <div>
        <span className={styles.label}>内容</span>
        <p style={{ whiteSpace: "pre-wrap" }}>{material.content}</p>
        {material.options?.map((option) => <div key={option}>{option}</div>)}
      </div>

      {material.reference_explanation ? (
        <div>
          <span className={styles.label}>参考解析（仅教师可见）</span>
          <p className={styles.muted}>{material.reference_explanation}</p>
        </div>
      ) : null}
    </div>
  );
}

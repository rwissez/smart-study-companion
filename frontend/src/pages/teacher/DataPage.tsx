import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { isDemo, getExportPreview, downloadExport, listQuality, simulateRun, uploadDataset, runAnalysis, createSession } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { EvidencePanel, type EvidenceTarget } from "../../components/evidence/EvidencePanel";
import { StatePanel } from "../../components/feedback/StatePanel";
import { formatDateTime, formatRuleVersion } from "../../lib/format";
import styles from "../../styles/ui.module.css";
import page from "../../styles/pages.module.css";
import view from "../../components/learning/TeacherViews.module.css";

const FILE_SLOTS = [
  { key: "requirements", label: "课程要求", file: "课程要求.csv" },
  { key: "materials", label: "题目与材料", file: "题目与材料.csv" },
  { key: "students", label: "模拟学生", file: "模拟学生.csv" },
  { key: "nodes", label: "测验节点", file: "测验节点.csv" },
  { key: "records", label: "作答记录", file: "作答记录.csv" },
  { key: "dictionary", label: "字段说明", file: "字段说明.csv" },
];

/** 数据管理：数据集、导入位、质量、运行与成果导出入口 */
export function DataPage() {
  const { session, start } = useSession();
  const { datasetId, runId, runs, setDatasetId } = useScope();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"dataset" | "quality" | "runs" | "exports">("dataset");
  const [files, setFiles] = useState<Record<string, File>>({});
  const [importFeedback, setImportFeedback] = useState("");
  const [exporting, setExporting] = useState(false);
  const [publishedOnly, setPublishedOnly] = useState(true);
  const [exportError, setExportError] = useState("");
  const [importing, setImporting] = useState(false);
  const [jobStatus, setJobStatus] = useState<"idle" | "running" | "done" | "failed" | "stopped">("idle");
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const pending = useRef(0);
  const uploadRequestId = useRef(crypto.randomUUID());
  const runRequestId = useRef(crypto.randomUUID());
  useEffect(() => {
    setEvidence(null);
    setExportError("");
    setJobStatus("idle");
    return () => { pending.current += 1; };
  }, [datasetId, runId]);

  const quality = useQuery({
    queryKey: queryKeys.quality(session, datasetId),
    queryFn: () => listQuality(datasetId as string),
    enabled: Boolean(session && datasetId),
    retry: false,
  });

  const exportPreview = useQuery({
    queryKey: [...queryKeys.exportPreview(session, runId), publishedOnly],
    queryFn: () => getExportPreview(runId as string, publishedOnly),
    enabled: Boolean(session && runId && activeTab === "exports"),
    retry: false,
  });

  const startSimulation = async (simulateFailure = false) => {
    if (!datasetId) return;
    const task = ++pending.current;
    setJobStatus("running");
    try {
      await simulateRun(datasetId);
      if (pending.current === task) setJobStatus(simulateFailure ? "failed" : "done");
    } catch {
      if (pending.current === task) setJobStatus("failed");
    }
  };

  const severityClass = {
    info: styles.badgeNeutral,
    warning: styles.badgeWarn,
    error: styles.badgeDanger,
  };

  return (
    <div className={styles.stackLg}>
        <div className={view.head}><div><div className={view.breadcrumbs}>首页 / 数据管理</div><h2>数据管理</h2></div></div>
      <div className={view.pageTabs}>
        {([["dataset", "数据集"], ["quality", "质量报告"], ["runs", "分析运行"], ["exports", "成果导出"]] as const).map(([id, label]) =>
          <button key={id} className={activeTab === id ? view.activePageTab : ""} onClick={() => setActiveTab(id)}>{label}</button>)}
      </div>

      <section className={page.section} style={{ display: activeTab === "dataset" ? undefined : "none" }}>
        <div className={page.sectionHead}>
          <h3>导入六个文件</h3>

        </div>
        <div className={page.fileGrid}>
          {FILE_SLOTS.map((slot) => (
            <div key={slot.key} className={page.fileSlot}>
              <span className={styles.label}>{slot.label}</span>
              <span className={page.mono}>{slot.file}</span>
              <label className={styles.field}>
                <span className={styles.small}>选择{slot.label}文件</span>
                <input type="file" accept=".csv,text/csv" disabled={importing}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    uploadRequestId.current = crypto.randomUUID();
                    runRequestId.current = crypto.randomUUID();
                    setFiles((previous) => {
                      const next = { ...previous };
                      if (file) next[slot.key] = file;
                      else delete next[slot.key];
                      return next;
                    });
                    setImportFeedback("");
                  }} />
              </label>
            </div>
          ))}
        </div>
        <button type="button" className={styles.button} disabled={importing} onClick={async () => {
          const missing = FILE_SLOTS.filter((slot) => !files[slot.key]);
          if (missing.length) {
            setImportFeedback(`请补选：${missing.map((slot) => slot.label).join("、")}`);
            return;
          }
          if (isDemo) {
            setImportFeedback("六个文件位已选齐。以下质量结果为固定示例，未读取文件内容。");
            return;
          }
          setImporting(true);
          let imported = false;
          try {
            const uploaded = await uploadDataset(files, uploadRequestId.current);
            imported = true;
            setImportFeedback("数据已导入，正在分析…");
            const newSession = await createSession("teacher", undefined, uploaded.dataset_id);
            start(newSession);
            setDatasetId(uploaded.dataset_id);
            const run = await runAnalysis(uploaded.dataset_id, uploaded.catalog_version, uploaded.node_ids, runRequestId.current);
            if (run.status === "failed") {
              runRequestId.current = crypto.randomUUID();
              throw new Error(run.error?.message ?? "分析失败，请检查数据后重试。");
            }
            await queryClient.invalidateQueries();
            setImportFeedback("数据已导入并完成分析，可返回班级总览查看。");
          } catch (error) {
            const message = error instanceof Error ? error.message : "操作失败";
            setImportFeedback(imported ? `数据已导入，但分析未完成：${message}。可以重试。` : `导入失败：${message}`);
          } finally {
            setImporting(false);
          }
        }}>
          {importing ? "正在导入和分析…" : isDemo ? "检查示例选件" : "导入并分析"}
        </button>
        {importFeedback && <p role="status">{importFeedback}</p>}
      </section>

      <section className={page.section} style={{ display: activeTab === "quality" ? undefined : "none" }}>
        <div className={page.sectionHead}>
          <h3>数据质量</h3>

        </div>

        {quality.isLoading ? <StatePanel kind="loading" title="正在读取质量问题" /> : null}
        {quality.error ? (
          <StatePanel
            kind="error"
            message={(quality.error as Error).message}
            onRetry={() => quality.refetch()}
          />
        ) : null}

        {quality.data ? (
          <div className={`${styles.card} ${styles.tableScroll}`} tabIndex={0} role="region" aria-label="数据质量表">
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>问题编号</th>
                  <th>严重程度</th>
                  <th>说明</th>
                  <th>涉及记录</th>
                  <th>处置</th>
                </tr>
              </thead>
              <tbody>
                {quality.data.map((issue) => (
                  <tr key={issue.issue_id}>
                    <td className={styles.num}>{issue.issue_id}</td>
                    <td>
                      <span className={`${styles.badge} ${severityClass[issue.severity]}`}>
                        {issue.severity === "error"
                          ? "错误"
                          : issue.severity === "warning"
                            ? "提醒"
                            : "说明"}
                      </span>
                    </td>
                    <td>{issue.message}</td>
                    <td className={styles.num}>{issue.record_ids.length ? issue.record_ids.map((id) => (
                      <button key={id} type="button" className={styles.button} disabled={!runId}
                        onClick={() => setEvidence({ kind: "record", id })}>{id}</button>
                    )) : "—"}</td>
                    <td>
                      {issue.action === "quarantine_event"
                        ? "隔离事件"
                        : issue.action === "exclude_score"
                          ? "排除计分"
                          : issue.action === "block_import"
                            ? "阻止导入"
                            : "保留原值"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className={page.section} style={{ display: activeTab === "runs" ? undefined : "none" }}>
        <div className={page.sectionHead}>
          <h3>分析运行</h3>
          <div className={styles.row}>
            <button
              type="button"
              className={styles.button}
              onClick={() => { void startSimulation(); }}
              disabled={!isDemo || jobStatus === "running" || !datasetId}
            >
              {jobStatus === "running" ? "模拟运行中…" : "模拟一次运行"}
            </button>
            {isDemo && jobStatus !== "running" && <button type="button" className={styles.button}
              disabled={!datasetId} onClick={() => { void startSimulation(true); }}>模拟一次失败</button>}
            {jobStatus === "running" && <button type="button" className={styles.button} onClick={() => {
              pending.current += 1;
              setJobStatus("stopped");
            }}>停止等待</button>}
          </div>
        </div>

        <div className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
          <div className={styles.row}>
            <span className={styles.badge}>{runs.find((item) => item.run_id === runId)?.status === "succeeded" ? "当前运行已完成" : "暂无可用结果"}</span>
            <span className={page.mono}>{runId ?? "未选择运行"}</span>
          </div>
          <p className={`${styles.small} ${styles.muted}`}>
            {isDemo ? "示例运行固定为已完成；模拟运行只演示状态变化。" : "导入数据后会自动创建分析运行。"}
          </p>
          {jobStatus === "failed" && <p role="alert">模拟任务失败，可重新运行。</p>}
          {jobStatus === "stopped" && <p role="status">已停止页面等待。该操作不取消任务。</p>}
          {jobStatus === "done" ? (
            <p className={`${styles.small}`}>刚才的模拟任务已结束（示例，不改变现有结果）。</p>
          ) : null}
        </div>
      </section>

      <section className={page.section} style={{ display: activeTab === "exports" ? undefined : "none" }}>
        <div className={page.sectionHead}>
          <h3>成果导出</h3>
          <span className={`${styles.small} ${styles.muted}`}>{isDemo ? "示例清单" : "当前分析结果 · ZIP"}</span>
        </div>
        {!isDemo && <label className={styles.field}>导出的建议版本
          <select className={styles.select} value={publishedOnly ? "published" : "teacher"}
            onChange={(event) => setPublishedOnly(event.target.value === "published")}>
            <option value="published">学生看到的已发布版本</option>
            <option value="teacher">教师当前版本（可含草稿）</option>
          </select>
        </label>}

        {exportPreview.isLoading ? <StatePanel kind="loading" title="正在读取成果包清单" /> : null}
        {exportPreview.error ? (
          <StatePanel
            kind="error"
            message={(exportPreview.error as Error).message}
            onRetry={() => exportPreview.refetch()}
          />
        ) : null}

        {exportPreview.data ? (
          <div className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
            <div className={styles.row}>
              <span className={styles.label}>生成时间</span>
              <span className={styles.num}>{formatDateTime(exportPreview.data.created_at)}</span>
              <span className={styles.label}>规则版本</span>
              <span>{formatRuleVersion(exportPreview.data.rule_version)}</span>
            </div>
            <ul className={styles.steps}>
              {exportPreview.data.files.map((file) => (
                <li key={file}>
                  <span className={page.mono}>{file}</span>
                </li>
              ))}
            </ul>
            <p className={`${styles.small} ${styles.muted}`}>
              {exportPreview.data.limitations.join("；")}
            </p>
            {exportError && <p role="alert">{exportError}</p>}
            <button type="button" className={styles.button} disabled={isDemo || exporting || !runId} onClick={async () => {
              if (!runId) return;
              setExporting(true);
              setExportError("");
              try {
                const url = URL.createObjectURL(await downloadExport(runId, publishedOnly));
                const link = document.createElement("a");
                link.href = url;
                link.download = "智慧学伴成果包.zip";
                link.click();
                window.setTimeout(() => URL.revokeObjectURL(url), 1000);
              } catch (error) {
                setExportError(error instanceof Error ? error.message : "下载失败，请重试。");
              } finally {
                setExporting(false);
              }
            }}>
              {isDemo ? "下载成果包（示例模式不可用）" : exporting ? "正在打包…" : "下载成果包"}
            </button>
          </div>
        ) : null}
      </section>
      <EvidencePanel target={evidence} runId={runId}
        catalogVersion={exportPreview.data?.catalog_version ?? null} onClose={() => setEvidence(null)} />
    </div>
  );
}

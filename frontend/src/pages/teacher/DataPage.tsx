import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { errorCode, errorRequestId, newIdempotencyKey, toErrorMessage } from "../../api/client";
import {
  confirmImport,
  createExport,
  createImport,
  downloadExport,
  getAnalysisQuality,
  getExportPreview,
  getKnowledgeQuality,
  listDatasets,
  listJobs,
  listQuality,
  listRuns,
} from "../../api/service";
import type { ImportReceipt, JobDetail, QualityIssue } from "../../api/types";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { StatePanel } from "../../components/feedback/StatePanel";
import { JobProgress } from "../../components/jobs/JobProgress";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { formatDate, jobStatusLabel } from "../../lib/format";
import {
  countLabel,
  datasetStateLabel,
  datasetStatusLabel,
  exportStatusLabel,
  formatBytes,
  jobKindLabel,
  jobStageLabel,
  knowledgeCountLabel,
  qualityActionLabel,
  qualityCodeLabel,
  severityLabel,
  tableLabel,
} from "../../lib/labels";
import { IconClose } from "../../components/icons";

const TABS = [
  { key: "dataset", label: "数据集" },
  { key: "jobs", label: "任务进度" },
  { key: "quality", label: "质量" },
  { key: "exports", label: "成果导出" },
] as const;

type TabKey = (typeof TABS)[number]["key"];
type ImportIntent = "new_family" | "append_to_family" | "auto_detect";

const ROLE_OPTIONS = ["requirements", "materials", "students", "nodes", "records", "dictionary"];
const MAX_FILE_BYTES = 32 * 1024 * 1024;
const ALLOWED_EXT = [".csv", ".xlsx", ".pdf"];

function parseTab(value: string | null): TabKey {
  return TABS.some((tab) => tab.key === value) ? (value as TabKey) : "dataset";
}

export function DataPage() {
  const { scopeKey, session, switchDataset } = useSession();
  const datasetId = scopeKey.datasetId;
  const canExport = session?.scope.capabilities.includes("export_run") === true;
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const [files, setFiles] = useState<File[]>([]);
  const [intent, setIntent] = useState<ImportIntent>("new_family");
  const [receipt, setReceipt] = useState<ImportReceipt | null>(null);
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [uploadError, setUploadError] = useState<{ text: string; requestId: string | null } | null>(null);
  const [fileWarning, setFileWarning] = useState<string | null>(null);
  const [confirmSwitch, setConfirmSwitch] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const importKey = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const datasets = useQuery({
    queryKey: queryKeys.datasets(scopeKey),
    queryFn: listDatasets,
  });
  const runs = useQuery({
    queryKey: queryKeys.runs(scopeKey),
    queryFn: () => listRuns(datasetId as string),
    enabled: Boolean(datasetId),
  });
  const jobs = useQuery({
    queryKey: queryKeys.jobs(scopeKey),
    queryFn: () => listJobs(datasetId as string, { pageSize: 30 }),
    enabled: Boolean(datasetId),
  });
  const quality = useQuery<{ items: QualityIssue[]; total: number }>({
    queryKey: queryKeys.quality(scopeKey),
    queryFn: () => listQuality(datasetId as string),
    enabled: Boolean(datasetId) && tab === "quality",
  });

  const run = runs.data?.find((item) => item.status === "succeeded") ?? runs.data?.[0] ?? null;
  const selectedJobId = searchParams.get("jobId") ?? jobs.data?.items[0]?.job_id ?? null;

  const analysisQuality = useQuery({
    queryKey: queryKeys.analysisQuality(scopeKey, run?.run_id ?? "", run?.node_ids[0] ?? "", null),
    queryFn: () => getAnalysisQuality(run!.run_id, { nodeId: run!.node_ids[0] }),
    enabled: Boolean(run && tab === "quality"),
  });

  const knowledgeQuality = useQuery({
    queryKey: queryKeys.knowledgeQuality(scopeKey),
    queryFn: () => getKnowledgeQuality(datasetId as string),
    enabled: Boolean(datasetId) && tab === "quality",
  });

  const exportsQuery = useQuery({
    queryKey: queryKeys.exports(scopeKey, run?.run_id ?? ""),
    queryFn: () => getExportPreview(run!.run_id),
    enabled: Boolean(run) && tab === "exports" && canExport,
  });

  const dataset = datasets.data?.find((item) => item.dataset_id === datasetId) ?? null;

  // 任务完成后运行与导出记录都会变化；切到质量/导出页签时确认一次最新运行。
  useEffect(() => {
    if (tab === "quality" || tab === "exports") void runs.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, datasetId]);

  const handleJobDone = (job: JobDetail) => {
    if (job.status !== "succeeded") return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.runs(scopeKey) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.exports(scopeKey, run?.run_id ?? "") });
    void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(scopeKey) });
  };

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    setFileWarning(null);
    const accepted: File[] = [];
    const rejected: string[] = [];
    Array.from(incoming).forEach((file) => {
      const lower = file.name.toLowerCase();
      const extension = ALLOWED_EXT.find((ext) => lower.endsWith(ext));
      if (!extension) {
        rejected.push(`${file.name}：只支持 CSV / XLSX / PDF`);
        return;
      }
      if (file.size > MAX_FILE_BYTES) {
        rejected.push(`${file.name}：超过 32MB`);
        return;
      }
      accepted.push(file);
    });
    setFiles((previous) => [...previous, ...accepted]);
    if (rejected.length) setFileWarning(rejected.join("；"));
    if (fileInput.current) fileInput.current.value = "";
  };

  const submitImport = async () => {
    if (files.length === 0) {
      setUploadError({ text: "请至少选择一个文件。", requestId: null });
      return;
    }
    setBusy(true);
    setUploadError(null);
    importKey.current = importKey.current ?? newIdempotencyKey("import");
    try {
      const result = await createImport(files, { intent }, importKey.current);
      setReceipt(result);
      setMappings(Object.fromEntries(result.files.map((file) => [file.file_id, file.role ?? ""])));
      setFiles([]);
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(scopeKey) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs(scopeKey) });
    } catch (reason) {
      setUploadError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  const submitConfirm = async () => {
    if (!receipt) return;
    setBusy(true);
    setUploadError(null);
    try {
      const result = await confirmImport(receipt.import_id, {
        file_mappings: receipt.files.map((file) => ({
          file_id: file.file_id,
          sheet_name: file.sheet_name,
          role: mappings[file.file_id],
        })),
        intent,
        dataset_family_id: receipt.dataset_family_id,
      });
      setReceipt(result);
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(scopeKey) });
    } catch (reason) {
      setUploadError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  const qualityLabel = (issue: QualityIssue) => `${qualityCodeLabel(issue.code)} · ${severityLabel(issue.severity)} · ${qualityActionLabel(issue.action)}`;

  const startExport = async () => {
    if (!run) return;
    setExportBusy(true);
    setExportError(null);
    try {
      const created = await createExport(run.run_id);
      setExportJobId(created.job_id);
    } catch (reason) {
      setExportError(toErrorMessage(reason));
    } finally {
      setExportBusy(false);
    }
  };

  const saveExport = async (exportId: string) => {
    setExportBusy(true);
    setExportError(null);
    try {
      const file = await downloadExport(exportId);
      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.filename ?? "zhanzhi-export.zip";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setExportError(toErrorMessage(reason));
    } finally {
      setExportBusy(false);
    }
  };

  const switchToImportedDataset = async () => {
    if (!receipt?.dataset_id) return;
    setBusy(true);
    setSwitchError(null);
    try {
      await switchDataset(receipt.dataset_id);
      setConfirmSwitch(false);
      const next = new URLSearchParams();
      next.set("tab", "jobs");
      if (receipt.job_id) next.set("jobId", receipt.job_id);
      setSearchParams(next);
    } catch (reason) {
      setSwitchError(toErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const systemQualityNote =
    "对外质量报告与系统运行质量还没有可用的公开报告，本节不显示模拟统计；任务失败与重试请看任务进度。";

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">数据管理</h1>
          <p className="pageSub">
            {dataset
              ? `${dataset.display_name || "当前数据集"} · ${dataset.student_count} 名学生 · ${datasetStatusLabel(dataset.status)}`
              : "选择或导入数据集"}
          </p>
        </div>
        <div className="pageActions">
          <button type="button" className="btn" onClick={() => { void datasets.refetch(); void jobs.refetch(); }}>刷新</button>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="数据管理页签">
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

      {tab === "dataset" ? (
        <section className="section" aria-label="数据集">
          <div className="sectionHead">
            <h2 className="sectionTitle">当前数据范围</h2>
            <span className="sectionHint">名称、范围与快照来自数据集摘要</span>
          </div>
          {datasets.isPending ? <StatePanel kind="loading" title="正在读取数据集" /> : null}
          {datasets.isError ? (
            <StatePanel kind="error" title="数据集读取失败" message={toErrorMessage(datasets.error)} requestId={errorRequestId(datasets.error)} onRetry={() => void datasets.refetch()} />
          ) : null}
          {dataset ? (
            <div className="qualityGrid">
              <dl className="kv">
                <dt>数据名称</dt><dd>{dataset.display_name ?? "未命名数据集"}</dd>
                <dt>状态</dt><dd>{datasetStatusLabel(dataset.status)}</dd>
                <dt>处理阶段</dt><dd>{datasetStateLabel(dataset.state)}</dd>
                <dt>学生 / 班级</dt><dd className="tabular">{dataset.student_count} / {dataset.class_count}</dd>
                <dt>课程目录</dt><dd>{dataset.catalog_version ? "已建立" : "尚未建立"}</dd>
                <dt>创建时间</dt><dd>{formatDate(dataset.created_at)}</dd>
                <dt>数据来源</dt><dd>{dataset.synthetic ? "合成数据" : "正式导入数据"}</dd>
              </dl>
              <div>
                <h3 className="paneTitle">表行数</h3>
                {Object.keys(dataset.table_row_counts ?? {}).length === 0 ? (
                  <p className="small muted">数据集摘要没有提供表行数。</p>
                ) : (
                  <table className="table">
                    <thead><tr><th scope="col">数据表</th><th scope="col" className="numeric">行数</th></tr></thead>
                    <tbody>
                      {Object.entries(dataset.table_row_counts).map(([table, count]) => (
                        <tr key={table}><td className="small">{tableLabel(table)}</td><td className="numeric">{count}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          ) : null}

          <div className="section" style={{ marginTop: 32 }}>
            <div className="sectionHead">
              <h2 className="sectionTitle">上传数据</h2>
              <span className="sectionHint">CSV / XLSX / PDF；单文件上限 32MB</span>
            </div>
            <div className="uploadBox">
              <div className="row">
                <label className="field">
                  <span className="fieldLabel">导入方式</span>
                  <select className="select" value={intent} onChange={(event) => setIntent(event.target.value as ImportIntent)}>
                    <option value="new_family">新建独立数据系列</option>
                    <option value="auto_detect">自动识别</option>
                    <option value="append_to_family">补充到现有系列</option>
                  </select>
                </label>
                <label className="field">
                  <span className="fieldLabel">选择文件</span>
                  <input ref={fileInput} className="input" type="file" multiple accept=".csv,.xlsx,.pdf" onChange={(event) => addFiles(event.target.files)} />
                </label>
              </div>
              {fileWarning ? <p className="fieldError" role="alert">{fileWarning}</p> : null}
              {files.length ? (
                <div className="fileList">
                  {files.map((file, index) => (
                    <div className="fileRow" key={`${file.name}-${index}`}>
                      <span className="fileRowName">{file.name}</span>
                      <span className="tabular small">{Math.ceil(file.size / 1024)} KB</span>
                      <span className="xs muted">{file.name.split(".").pop()?.toUpperCase()}</span>
                      <button type="button" className="iconBtn" aria-label={`移除 ${file.name}`} onClick={() => setFiles((previous) => previous.filter((_, position) => position !== index))}>
                        <IconClose size={16} />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="row">
                <button type="button" className="btn btnPrimary" onClick={() => void submitImport()} disabled={busy || files.length === 0}>
                  {busy ? "上传中…" : "上传并开始处理"}
                </button>
                <span className="small muted">上传后由服务端分类并自动排入 A→B→C 任务；前端不判定文件角色。</span>
              </div>
              {uploadError ? (
                <p className="fieldError" role="alert">
                  {uploadError.text}
                  {uploadError.requestId ? `（请求编号 ${uploadError.requestId}）` : ""}
                </p>
              ) : null}
            </div>

            {receipt ? (
              <div className="section" style={{ marginTop: 24 }}>
                <div className="sectionHead">
                  <h2 className="sectionTitle">导入回执</h2>
                  <span className="sectionHint">
                    {receipt.classification_status === "needs_confirmation"
                      ? "需要确认文件角色"
                      : receipt.classification_status === "confirmed"
                        ? "文件角色已确认"
                        : "已自动识别"}
                    {" · "}
                    {receipt.input_status === "ready" ? "输入完整" : "输入不完整"}
                  </span>
                </div>
                <dl className="kv">
                  <dt>数据集</dt><dd>{receipt.dataset_id ? "已创建" : "未创建（缺少必要输入）"}</dd>
                  <dt>后台任务</dt><dd>{receipt.job_id ? "已排入处理队列" : "未排入任务"}</dd>
                  <dt>缺失输入</dt><dd>{receipt.missing_inputs.length ? receipt.missing_inputs.map((item) => tableLabel(item)).join("、") : "无"}</dd>
                </dl>
                <div className="tableWrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">文件</th>
                        <th scope="col">格式</th>
                        <th scope="col">角色</th>
                        <th scope="col">状态</th>
                        <th scope="col">原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {receipt.files.map((file) => (
                        <tr key={file.file_id}>
                          <td className="small breakAnywhere">{file.original_name}</td>
                          <td className="small">{file.format}</td>
                          <td className="small">
                            {receipt.classification_status === "needs_confirmation" ? (
                              <select
                                className="select"
                                value={mappings[file.file_id] ?? ""}
                                onChange={(event) => setMappings((previous) => ({ ...previous, [file.file_id]: event.target.value }))}
                                aria-label={`${file.original_name} 的角色`}
                              >
                                <option value="">请选择角色</option>
                                {ROLE_OPTIONS.map((role) => <option key={role} value={role}>{tableLabel(role)}</option>)}
                              </select>
                            ) : (
                              file.role ?? "由服务端识别"
                            )}
                          </td>
                          <td className="small">{file.status === "ready" ? "已识别" : file.status === "blocked" ? "被阻止" : file.status === "incomplete" ? "缺内容" : "待确认"}</td>
                          <td className="small">{file.reason ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {receipt.classification_status === "needs_confirmation" ? (
                  <div className="row" style={{ marginTop: 12 }}>
                    <button type="button" className="btn btnPrimary" onClick={() => void submitConfirm()} disabled={busy}>
                      {busy ? "确认中…" : "确认文件角色并继续"}
                    </button>
                    <span className="small muted">确认前不会生成正式快照；重复提交会返回原记录。</span>
                  </div>
                ) : null}
                {receipt.quality_summary.length ? (
                  <div style={{ marginTop: 12 }}>
                    <h3 className="paneTitle">导入质量提示</h3>
                    <ul>
                      {receipt.quality_summary.map((issue) => (
                        <li key={issue.issue_id} className="small">{issue.message}（{qualityLabel(issue)}）</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {receipt.job_id ? (
                  <div className="row" style={{ marginTop: 12 }}>
                    {receipt.dataset_id && receipt.dataset_id !== datasetId ? (
                      <StatePanel
                        kind="not-ready"
                        title="导入已创建新数据集"
                        message="当前会话仍绑定在旧数据集上，读取新数据集的学情与任务会被服务端拒绝。需要切换会话数据范围后才能查看这次任务。"
                        action={
                          <button type="button" className="btn btnPrimary" onClick={() => setConfirmSwitch(true)}>
                            切换数据集并查看任务
                          </button>
                        }
                      />
                    ) : (
                      <button
                        type="button"
                        className="btn"
                        onClick={() => {
                          const next = new URLSearchParams(searchParams);
                          next.set("tab", "jobs");
                          next.set("jobId", receipt.job_id as string);
                          setSearchParams(next);
                        }}
                      >
                        查看任务进度
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>
      ) : null}

      {tab === "jobs" ? (
        <section className="section" aria-label="任务进度">
          <div className="sectionHead">
            <h2 className="sectionTitle">任务链</h2>
            <span className="sectionHint">当前数据集最近的任务；状态与步骤来自服务端</span>
          </div>
          {!datasetId ? (
            <StatePanel kind="empty" title="还没有可用数据集" message="先上传数据，服务端会自动排入导入与分析任务。" />
          ) : jobs.isPending ? (
            <StatePanel kind="loading" title="正在读取任务" />
          ) : jobs.isError ? (
            <StatePanel kind="error" title="任务读取失败" message={toErrorMessage(jobs.error)} requestId={errorRequestId(jobs.error)} onRetry={() => void jobs.refetch()} />
          ) : (
            <div className="grid2">
              <div>
                <div className="jobList stackSm">
                  {(jobs.data?.items ?? []).length === 0 ? <p className="small muted">这个数据集还没有任务记录。</p> : null}
                  {(jobs.data?.items ?? []).map((job: JobDetail) => (
                    <button
                      key={job.job_id}
                      type="button"
                      className={selectedJobId === job.job_id ? "treeItem treeItemActive" : "treeItem"}
                      onClick={() => {
                        const next = new URLSearchParams(searchParams);
                        next.set("tab", "jobs");
                        next.set("jobId", job.job_id);
                        setSearchParams(next);
                      }}
                    >
                      <span>{jobKindLabel(job.kind)} · {jobStatusLabel(job.status)}</span>
                      <span className="treeItemMeta">{formatDate(job.created_at)} · {jobStageLabel(job.stage)}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                {selectedJobId ? (
                  <JobProgress key={selectedJobId} jobId={selectedJobId} onJob={handleJobDone} />
                ) : (
                  <p className="small muted">选择左侧任务查看真实进度与事件。</p>
                )}
              </div>
            </div>
          )}
        </section>
      ) : null}

      {tab === "quality" ? (
        <section className="section" aria-label="质量报告">
          <div className="sectionHead">
            <h2 className="sectionTitle">质量报告</h2>
            <span className="sectionHint">数据 / 知识体系 / 学情 / 学习方案 / 系统运行</span>
          </div>
          <div className="qualityGrid">
            <div>
              <h3 className="paneTitle">数据质量</h3>
              {quality.isPending ? <StatePanel kind="loading" title="正在读取数据质量" /> : null}
              {quality.isError ? (
                <StatePanel kind="error" title="数据质量读取失败" message={toErrorMessage(quality.error)} />
              ) : null}
              {quality.data ? (
                quality.data.items.length === 0 ? (
                  <p className="small muted">这次导入没有质量提示；空列表不代表数据完美。</p>
                ) : (
                  <table className="table">
                    <thead><tr><th scope="col">问题</th><th scope="col">严重度</th><th scope="col">处理</th><th scope="col">说明</th></tr></thead>
                    <tbody>
                      {quality.data.items.map((issue) => (
                        <tr key={issue.issue_id}>
                          <td className="small">{qualityCodeLabel(issue.code)}</td>
                          <td className="small">{severityLabel(issue.severity)}</td>
                          <td className="small">{qualityActionLabel(issue.action)}</td>
                          <td className="small">{issue.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              ) : null}
            </div>
            <div>
              <h3 className="paneTitle">知识体系质量</h3>
              {knowledgeQuality.isPending ? <p className="small muted">正在读取…</p> : null}
              {knowledgeQuality.isError ? (
                <StatePanel
                  kind={errorCode(knowledgeQuality.error) === "RESULT_NOT_READY" ? "not-ready" : "error"}
                  title={errorCode(knowledgeQuality.error) === "RESULT_NOT_READY" ? "知识质量报告尚未就绪" : "知识质量读取失败"}
                  message={toErrorMessage(knowledgeQuality.error)}
                />
              ) : null}
              {knowledgeQuality.data ? (
                <div className="stackSm">
                  <div className="row">
                    {Object.entries(knowledgeQuality.data.counts)
                      .map(([key, value]) => ({ label: knowledgeCountLabel(key), value }))
                      .filter((item): item is { label: string; value: number } => Boolean(item.label))
                      .slice(0, 10)
                      .map((item) => (
                        <span className="tag" key={item.label}>{item.label} {String(item.value)}</span>
                      ))}
                  </div>
                  {knowledgeQuality.data.limitations.length ? (
                    <p className="small muted">限制：{knowledgeQuality.data.limitations.join("；")}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div>
              <h3 className="paneTitle">学情质量</h3>
              {analysisQuality.isPending && run ? <p className="small muted">正在读取…</p> : null}
              {!run ? <p className="small muted">还没有分析运行。</p> : null}
              {analysisQuality.isError ? (
                <StatePanel kind="error" title="学情质量读取失败" message={toErrorMessage(analysisQuality.error)} />
              ) : null}
              {analysisQuality.data ? (
                <div className="stackSm">
                  <div className="row">
                    {Object.entries((analysisQuality.data.counts as Record<string, number>) ?? {})
                      .map(([key, value]) => ({ label: countLabel(key), value }))
                      .filter((item): item is { label: string; value: number } => Boolean(item.label))
                      .map((item) => (
                        <span className="tag" key={item.label}>{item.label} {item.value}</span>
                      ))}
                  </div>
                  <p className="small muted">生成时间 {formatDate(String(analysisQuality.data.generated_at ?? ""))}</p>
                  {Array.isArray(analysisQuality.data.limitations) ? (
                    <p className="small muted">限制：{(analysisQuality.data.limitations as string[]).join("；")}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div>
              <h3 className="paneTitle">学习方案质量</h3>
              <p className="small muted">学习方案质量报告还没有接入页面，这里不显示模拟百分比。</p>
              <h3 className="paneTitle" style={{ marginTop: 16 }}>系统运行</h3>
              <p className="small muted">{systemQualityNote}</p>
            </div>
          </div>
        </section>
      ) : null}

      {tab === "exports" ? (
        <section className="section" aria-label="成果导出">
          <div className="sectionHead">
            <h2 className="sectionTitle">成果导出</h2>
            <span className="sectionHint">按正式覆盖范围与合法证据生成，本次快照不受后续改动影响</span>
          </div>
          {!run ? (
            <StatePanel kind="empty" title="还没有可导出的分析运行" message="分析完成后才能生成成果导出。" />
          ) : !canExport ? (
            <StatePanel
              kind="not-ready"
              title="当前会话没有导出权限"
              message="服务端的导出接口要求 export_run 能力，而当前教师会话没有获得该能力，因此页面不显示生成按钮，也不会伪造导出结果。"
              action={<span className="xs muted">可以继续查看质量报告与任务进度；导出能力就绪后这里会自动出现生成入口。</span>}
            />
          ) : (
            <div className="stackLg">
              <div className="row">
                <button type="button" className="btn btnPrimary" onClick={() => void startExport()} disabled={exportBusy}>
                  {exportBusy ? "处理中…" : "生成导出"}
                </button>
                <span className="small muted">导出包含正式结果、字段字典、证据与案例；不会调用模型。</span>
              </div>
              {exportError ? <p className="fieldError" role="alert">{exportError}</p> : null}

              {exportJobId ? (
                <div className="section">
                  <div className="sectionHead">
                    <h2 className="sectionTitle">导出任务</h2>
                  </div>
                  <JobProgress
                    key={exportJobId}
                    jobId={exportJobId}
                    onJob={(job) => {
                      handleJobDone(job);
                      if (job.status === "succeeded") {
                        void exportsQuery.refetch();
                      }
                    }}
                  />
                </div>
              ) : null}

              <div>
                <h3 className="paneTitle">已生成的导出</h3>
                {exportsQuery.isPending ? <StatePanel kind="loading" title="正在读取导出记录" /> : null}
                {exportsQuery.isError ? (
                  <StatePanel kind="error" title="导出记录读取失败" message={toErrorMessage(exportsQuery.error)} requestId={errorRequestId(exportsQuery.error)} onRetry={() => void exportsQuery.refetch()} />
                ) : null}
                {exportsQuery.data && exportsQuery.data.items.length === 0 ? (
                  <p className="small muted">这次运行还没有导出记录。需要时点击“生成导出”。</p>
                ) : null}
                {exportsQuery.data && exportsQuery.data.items.length > 0 ? (
                  <div className="tableWrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th scope="col">状态</th>
                          <th scope="col">生成时间</th>
                          <th scope="col" className="numeric">大小</th>
                          <th scope="col">检查结果</th>
                          <th scope="col">下载</th>
                        </tr>
                      </thead>
                      <tbody>
                        {exportsQuery.data.items.map((item) => {
                          const passed = item.check_report?.passed === true;
                          return (
                            <tr key={item.export_id}>
                              <td className="small">{exportStatusLabel(item.status)}</td>
                              <td className="small">{formatDate(item.created_at)}</td>
                              <td className="numeric">{formatBytes(item.bytes)}</td>
                              <td className="small">{passed ? "校验通过" : "校验有提示"}</td>
                              <td>
                                <button
                                  type="button"
                                  className="btn btnSm btnGhost"
                                  disabled={item.status !== "ready" || exportBusy}
                                  onClick={() => void saveExport(item.export_id)}
                                >
                                  下载 ZIP
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </section>
      ) : null}

      <ConfirmDialog
        open={confirmSwitch}
        title="切换到新导入的数据集"
        confirmLabel="切换并重建会话"
        onConfirm={() => void switchToImportedDataset()}
        onCancel={() => { setConfirmSwitch(false); setSwitchError(null); }}
        busy={busy}
      >
        <p>切换后旧数据集的查询缓存会被清空并重建会话，未提交的表单会丢失。</p>
        {switchError ? <p className="fieldError" role="alert">{switchError}</p> : null}
      </ConfirmDialog>
    </div>
  );
}

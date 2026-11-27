import { useCallback, useEffect, useRef, useState } from "react";
import { errorRequestId, newIdempotencyKey, toErrorMessage } from "../../api/client";
import { cancelJob, getJob, getJobEvents, retryJob } from "../../api/service";
import type { JobDetail, JobEvent } from "../../api/types";
import { formatDate, jobStatusLabel } from "../../lib/format";
import { jobKindLabel, jobStageLabel } from "../../lib/labels";
import { StatePanel } from "../feedback/StatePanel";

interface Props {
  jobId: string;
  onJob?: (job: JobDetail) => void;
}

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

function publicationLabel(status: string | null): string | null {
  if (!status) return null;
  if (status === "published") return "已发布";
  if (status === "unpublished") return "未发布";
  return "发布状态待确认";
}
const STEP_TONE: Record<string, string> = {
  succeeded: "badgeSuccess",
  failed: "badgeError",
  running: "badgeWarning",
  queued: "badgeNeutral",
  skipped: "badgeUnknown",
};

/**
 * 任务进度：先读当前状态，再按 after_sequence 增量读事件并按 sequence 去重。
 * 只画服务端给过的步骤总量，未知总量不画百分比；终态停止轮询。
 */
export function JobProgress({ jobId, onJob }: Props) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const lastSequence = useRef(0);
  const startedAt = useRef(Date.now());
  const onJobRef = useRef(onJob);
  onJobRef.current = onJob;

  const poll = useCallback(async () => {
    try {
      const next = await getJob(jobId);
      setJob(next);
      onJobRef.current?.(next);
      const page = await getJobEvents(jobId, lastSequence.current);
      if (page.items.length) {
        setEvents((previous) => {
          const known = new Set(previous.map((item) => item.sequence));
          const merged = [...previous, ...page.items.filter((item) => !known.has(item.sequence))];
          merged.sort((a, b) => a.sequence - b.sequence);
          lastSequence.current = merged[merged.length - 1]?.sequence ?? lastSequence.current;
          return merged;
        });
      }
      setError(null);
      if (!TERMINAL.has(next.status) && Date.now() - startedAt.current > 60_000) {
        setNotice("任务仍在运行，可以离开这个页面，后台任务不会中断。");
      }
    } catch (reason) {
      setError(reason);
    }
  }, [jobId]);

  useEffect(() => {
    lastSequence.current = 0;
    setEvents([]);
    setJob(null);
    startedAt.current = Date.now();
    let timer: number | undefined;
    let cancelled = false;
    const tick = async () => {
      await poll();
      if (cancelled) return;
      timer = window.setTimeout(tick, document.visibilityState === "hidden" ? 10_000 : 2_000);
    };
    void tick();
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [jobId, poll]);

  const running = job ? !TERMINAL.has(job.status) : false;

  const onCancel = async () => {
    if (!job) return;
    setBusy(true);
    setActionError(null);
    try {
      const next = await cancelJob(job.job_id);
      setJob(next);
    } catch (reason) {
      setActionError(toErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const onRetry = async () => {
    if (!job) return;
    setBusy(true);
    setActionError(null);
    try {
      const next = await retryJob(job.job_id, newIdempotencyKey("retry-job"));
      setJob(next);
      setEvents([]);
      lastSequence.current = 0;
    } catch (reason) {
      setActionError(toErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  if (!job && error) {
    return (
      <StatePanel
        kind="error"
        title="任务状态读取失败"
        message={toErrorMessage(error)}
        requestId={errorRequestId(error)}
        onRetry={() => void poll()}
      />
    );
  }
  if (!job) return <StatePanel kind="loading" title="正在读取任务状态" />;

  const doneSteps = job.steps.filter((step) => step.status === "succeeded" || step.status === "skipped").length;
  const knownTotal = job.steps.length;
  const showProgress = knownTotal > 0 && running;

  return (
    <div className="stack">
      <div className="rowBetween">
        <div>
          <h3 className="sectionTitle">任务进度</h3>
          <div className="jobMeta">
            <span>{jobKindLabel(job.kind)}</span>
            <span className={`badge ${STEP_TONE[job.status] ?? "badgeNeutral"}`}>{jobStatusLabel(job.status)}</span>
            <span>阶段：{jobStageLabel(job.stage)}</span>
            <span>更新：{formatDate(job.updated_at)}</span>
            {publicationLabel(job.publication_status) ? <span>{publicationLabel(job.publication_status)}</span> : null}
          </div>
        </div>
        <div className="row">
          {running ? (
            <button type="button" className="btn btnSm" onClick={() => void onCancel()} disabled={busy || job.cancel_requested}>
              {job.cancel_requested ? "已请求取消" : "取消任务"}
            </button>
          ) : null}
          {job.retry.can_retry ? (
            <button type="button" className="btn btnSm" onClick={() => void onRetry()} disabled={busy}>
              重试一次
            </button>
          ) : job.status === "failed" && job.retry.retry_block_reason ? (
            <span className="xs muted">不可重试：{job.retry.retry_block_reason}</span>
          ) : null}
        </div>
      </div>

      {showProgress ? (
        <div className="stackSm">
          <div className="rowBetween small">
            <span>步骤进度（服务器返回的步骤总数）</span>
            <span className="tabular">{doneSteps} / {knownTotal}</span>
          </div>
          <div className="progressTrack">
            <div className="progressFill" style={{ width: `${(doneSteps / knownTotal) * 100}%` }} />
          </div>
        </div>
      ) : null}

      {job.error ? (
        <StatePanel
          kind="error"
          title="任务失败"
          message={job.error.message ?? job.error.code ?? "服务没有返回失败原因。"}
        />
      ) : null}

      <div className="stepList">
        {job.steps.map((step) => (
          <div className="stepRow" key={step.step_key}>
            <span>{step.label || step.step_key}</span>
            <span className={`badge ${STEP_TONE[step.status] ?? "badgeNeutral"}`}>{jobStatusLabel(step.status)}</span>
            <span className="small muted">{step.message ?? (step.ended_at ? `完成于 ${formatDate(step.ended_at)}` : "—")}</span>
          </div>
        ))}
      </div>

      {notice ? <p className="small muted">{notice}</p> : null}
      {actionError ? <p className="fieldError" role="alert">{actionError}</p> : null}

      {events.length ? (
        <div>
          <h4 className="paneTitle">事件（按 sequence 去重）</h4>
          <ul className="stackSm" style={{ listStyle: "none", padding: 0 }}>
            {events.slice(-8).map((event) => (
              <li key={event.sequence} className="small">
                <span className="tabular muted">#{event.sequence}</span> {jobStageLabel(event.stage)} · {event.message} · {formatDate(event.occurred_at)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="small muted">暂无增量事件。</p>
      )}
      {error ? <p className="xs muted">最近一次轮询失败：{toErrorMessage(error)}</p> : null}
    </div>
  );
}

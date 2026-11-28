"""统一任务服务：入队、领取、心跳、完成、失败、取消与一次教师重试。

关键约束（01 §9.1、06 §6）：

* 任务状态来自真实步骤，不以"已排队"冒充进度；
* 领取用 CAS（queued → running + lease），过期租约不可再写正式结果；
* 完成/失败与业务效应同事务，失败保留原因，不覆盖历史；
* 教师手动重试生成**关联子任务**，原失败任务保持可读，每个根只允许一次。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError, JobDetail, JobEvent, JobScope, StepView, RetryView
from study_companion.db import models

LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20
MAX_ATTEMPTS = 2


# --------------------------------------------------------------------------- #
# 时间与键
# --------------------------------------------------------------------------- #


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat(timespec="milliseconds")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def scope_key(scope: dict[str, Any]) -> str:
    return digest(scope)[:32]


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 事件与步骤
# --------------------------------------------------------------------------- #


def append_event(
    db: Session, job: models.Job, *, stage: str, status: str, message: str = ""
) -> models.JobEventRow:
    last = db.scalar(
        select(models.JobEventRow.sequence)
        .where(models.JobEventRow.job_id == job.id)
        .order_by(models.JobEventRow.sequence.desc())
        .limit(1)
    )
    row = models.JobEventRow(
        job_id=job.id,
        sequence=(last or 0) + 1,
        stage=stage,
        status=status,
        message=message,
        occurred_at=now_iso(),
    )
    db.add(row)
    job.stage = stage
    job.updated_at = row.occurred_at
    db.flush()
    return row


def upsert_step(
    db: Session,
    job: models.Job,
    step_key: str,
    *,
    label: str = "",
    status: str = "running",
    message: str = "",
    error: dict[str, Any] | None = None,
) -> models.JobStep:
    row = db.get(models.JobStep, (job.id, step_key))
    if row is None:
        row = models.JobStep(
            job_id=job.id,
            step_key=step_key,
            label=label or step_key,
            status=status,
        )
        db.add(row)
    if label:
        row.label = label
    row.status = status
    row.message = message
    if status == "running" and row.started_at is None:
        row.started_at = now_iso()
        row.attempt_count = (row.attempt_count or 0) + 1
    if status in {"succeeded", "failed", "skipped", "cancelled"}:
        row.ended_at = now_iso()
    if error is not None:
        row.error_json = error
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #


def enqueue(
    db: Session,
    *,
    kind: str,
    scope: dict[str, Any],
    payload: dict[str, Any] | None = None,
    input_digest: str | None = None,
    dataset_id: str | None = None,
    student_id: str | None = None,
    requested_by: str | None = None,
    job_id: str | None = None,
    dependency_generation: int = 0,
) -> models.Job:
    """幂等入队：同 kind+scope+输入+依赖代只保留一个初始任务。"""

    key = scope_key(scope)
    input_hash = input_digest or digest(payload or {})
    existing = db.scalar(
        select(models.Job).where(
            models.Job.kind == kind,
            models.Job.scope_key == key,
            models.Job.input_digest == input_hash,
            models.Job.dependency_generation == dependency_generation,
            models.Job.manual_retry_no == 0,
        )
    )
    if existing is not None:
        return existing

    stamp = now_iso()
    job = models.Job(
        id=job_id or new_id("job"),
        dataset_id=dataset_id,
        student_id=student_id,
        status="queued",
        kind=kind,
        scope_key=key,
        scope_json=dict(scope),
        input_digest=input_hash,
        dependency_generation=dependency_generation,
        available_at=stamp,
        created_at=stamp,
        updated_at=stamp,
        payload_json=dict(payload or {}),
        requested_by=requested_by,
        max_attempts=MAX_ATTEMPTS,
        retry_eligible=1,
    )
    db.add(job)
    db.flush()
    append_event(db, job, stage="queued", status="queued", message="任务已入队")
    return job


def enqueue_outbox(
    db: Session,
    *,
    kind: str,
    scope: dict[str, Any],
    payload: dict[str, Any] | None = None,
    business_key: str | None = None,
) -> models.Outbox:
    key = business_key or digest({"kind": kind, "scope": scope, "payload": payload or {}})
    existing = db.scalar(select(models.Outbox).where(models.Outbox.business_key == key))
    if existing is not None:
        return existing
    row = models.Outbox(
        outbox_id=new_id("obx"),
        business_key=key,
        kind=kind,
        scope_key=scope_key(scope),
        payload_json={"scope": scope, "payload": payload or {}},
        state="pending",
        available_at=now_iso(),
        created_at=now_iso(),
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 领取与终态
# --------------------------------------------------------------------------- #


def claim_next(db: Session, lease_seconds: int = LEASE_SECONDS) -> models.Job | None:
    stamp = now()
    candidates = db.scalars(
        select(models.Job)
        .where(models.Job.status == "queued", models.Job.available_at <= stamp.isoformat())
        .order_by(models.Job.available_at, models.Job.created_at)
        .limit(5)
    ).all()
    for job in candidates:
        blocking = db.execute(
            select(models.JobDependency.depends_on_job_id)
            .where(models.JobDependency.job_id == job.id)
        ).scalars().all()
        if blocking:
            pending = db.scalar(
                select(models.Job).where(
                    models.Job.id.in_(blocking),
                    models.Job.status.in_(("queued", "running", "failed")),
                )
            )
            if pending is not None:
                continue
        token = new_id("lease")
        result = db.execute(
            update(models.Job)
            .where(models.Job.id == job.id, models.Job.status == "queued")
            .values(
                status="running",
                lease_token=token,
                lease_until=(stamp + timedelta(seconds=lease_seconds)).isoformat(),
                attempt_count=models.Job.attempt_count + 1,
                updated_at=stamp.isoformat(),
            )
        )
        if result.rowcount != 1:
            continue
        db.flush()
        db.refresh(job)
        append_event(db, job, stage="running", status="running", message="任务开始执行")
        return job
    return None


def renew_lease(db: Session, job: models.Job, lease_seconds: int = LEASE_SECONDS) -> bool:
    result = db.execute(
        update(models.Job)
        .where(models.Job.id == job.id, models.Job.lease_token == job.lease_token, models.Job.status == "running")
        .values(lease_until=(now() + timedelta(seconds=lease_seconds)).isoformat())
    )
    db.flush()
    return result.rowcount == 1


def finalize_success(db: Session, job: models.Job, result: dict[str, Any] | None) -> models.Job:
    stamp = now_iso()
    job.status = "succeeded"
    job.result_json = result or {}
    job.result_ref = job.result_ref or (result or {}).get("result_ref")
    job.lease_token = None
    job.lease_until = None
    job.updated_at = stamp
    append_event(db, job, stage="done", status="succeeded", message="任务完成")
    db.flush()
    return job


def finalize_failure(
    db: Session, job: models.Job, *, code: str, message: str, retryable: bool | None = None
) -> models.Job:
    stamp = now_iso()
    job.status = "failed"
    job.error_json = {"code": code, "message": message}
    job.lease_token = None
    job.lease_until = None
    job.updated_at = stamp
    if retryable is None:
        retryable = job.attempt_count < job.max_attempts
    job.retry_eligible = 1 if retryable else 0
    job.retry_block_reason = None if retryable else "NOT_RETRYABLE"
    append_event(db, job, stage="failed", status="failed", message=message)
    db.flush()
    return job


def request_cancel(db: Session, job: models.Job) -> models.Job:
    if job.status in {"succeeded", "failed", "cancelled"}:
        return job
    job.cancel_requested = 1
    if job.status == "queued":
        job.status = "cancelled"
        job.updated_at = now_iso()
        append_event(db, job, stage="cancelled", status="cancelled", message="任务已取消")
    else:
        append_event(db, job, stage="cancel_requested", status="running", message="已请求取消，等待当前步骤结束")
    db.flush()
    return job


def cancel_requested(job: models.Job) -> bool:
    return bool(job.cancel_requested)


def recover_expired(db: Session) -> int:
    """过期租约：回到 queued 或判失败，绝不留下永久 running。"""

    stamp = now()
    rows = db.scalars(select(models.Job).where(models.Job.status == "running")).all()
    recovered = 0
    for job in rows:
        lease = _parse(job.lease_until)
        if lease is None or lease > stamp:
            continue
        if job.attempt_count < job.max_attempts:
            job.status = "queued"
            job.lease_token = None
            job.lease_until = None
            job.available_at = stamp.isoformat()
            job.updated_at = stamp.isoformat()
            append_event(db, job, stage="requeued", status="queued", message="租约过期，重新排队")
        else:
            finalize_failure(
                db, job, code="LEASE_EXPIRED", message="任务执行超时且重试次数已用尽"
            )
        recovered += 1
    if recovered:
        db.flush()
    return recovered


# --------------------------------------------------------------------------- #
# 教师一次手动重试
# --------------------------------------------------------------------------- #


def manual_retry(db: Session, job: models.Job, *, actor: str) -> models.Job:
    if job.status != "failed":
        raise DomainError("JOB_RETRY_NOT_AVAILABLE", "只有最终失败的任务可以手动重试")
    if not job.retry_eligible:
        raise DomainError(
            "JOB_RETRY_NOT_AVAILABLE",
            f"该失败不可重试：{job.retry_block_reason or '已判定为不可恢复'}",
        )
    root_id = job.root_job_id or job.id
    used = db.scalar(
        select(models.Job).where(
            models.Job.root_job_id == root_id, models.Job.manual_retry_no == 1
        )
    )
    if used is not None:
        raise DomainError("JOB_RETRY_NOT_AVAILABLE", "该任务链已经使用过一次手动重试")

    stamp = now_iso()
    child = models.Job(
        id=new_id("job"),
        dataset_id=job.dataset_id,
        student_id=job.student_id,
        status="queued",
        kind=job.kind,
        scope_key=job.scope_key,
        scope_json=dict(job.scope_json or {}),
        input_digest=job.input_digest,
        dependency_generation=job.dependency_generation,
        available_at=stamp,
        created_at=stamp,
        updated_at=stamp,
        payload_json=dict(job.payload_json or {}),
        requested_by=actor,
        max_attempts=job.max_attempts,
        retry_eligible=1,
        retry_of_job_id=job.id,
        root_job_id=root_id,
        manual_retry_no=1,
    )
    db.add(child)
    db.flush()
    append_event(
        db,
        child,
        stage="queued",
        status="queued",
        message=f"教师手动重试，原任务 {job.id} 的失败记录保留",
    )
    db.add(
        models.SystemAudit(
            audit_id=new_id("audit"),
            scope_key=job.scope_key,
            actor_ref=actor,
            action="job_manual_retry",
            subject_ref=job.id,
            before_ref=job.id,
            after_ref=child.id,
            details_json={
                "root_job_id": root_id,
                "original_error": job.error_json or {},
                "original_attempts": job.attempt_count,
            },
            created_at=stamp,
        )
    )
    db.flush()
    return child


# --------------------------------------------------------------------------- #
# 读取投影
# --------------------------------------------------------------------------- #


def _retry_view(db: Session, job: models.Job) -> RetryView:
    root_id = job.root_job_id or job.id
    child = db.scalar(
        select(models.Job).where(
            models.Job.root_job_id == root_id, models.Job.manual_retry_no == 1
        )
    )
    used = 1 if child is not None else 0
    can_retry = (
        job.status == "failed"
        and bool(job.retry_eligible)
        and used == 0
        and job.manual_retry_no == 0
    )
    reason = None
    if not can_retry:
        if job.status != "failed":
            reason = "任务尚未最终失败"
        elif used:
            reason = "该任务链已使用过一次手动重试"
        elif not job.retry_eligible:
            reason = job.retry_block_reason or "失败被判定为不可恢复"
        elif job.manual_retry_no == 1:
            reason = "重试产生的子任务不能再次手动重试"
    return RetryView(
        root_job_id=root_id,
        retry_of_job_id=job.retry_of_job_id,
        retry_job_id=child.id if child else None,
        manual_retry_count=used,
        can_retry=can_retry,
        retry_block_reason=reason,
    )


def to_detail(db: Session, job: models.Job) -> JobDetail:
    steps = db.scalars(
        select(models.JobStep).where(models.JobStep.job_id == job.id)
    ).all()
    warnings = [item for item in (job.warnings_json or []) if isinstance(item, dict)]
    error = job.error_json if isinstance(job.error_json, dict) else None
    result = job.result_json if isinstance(job.result_json, dict) else None
    publication_status = None
    review_status = None
    plan_version = None
    if result:
        publication_status = result.get("publication_status")
        review_status = result.get("review_status")
        plan_version = result.get("plan_version")
    return JobDetail(
        job_id=job.id,
        kind=job.kind or "unknown",
        status=job.status,  # type: ignore[arg-type]
        stage=job.stage,
        scope=JobScope(**(job.scope_json or {})),
        created_at=job.created_at,
        updated_at=job.updated_at,
        result_ref=job.result_ref,
        error=error,
        warnings=warnings,
        steps=[
            StepView(
                step_key=row.step_key,
                label=row.label,
                status=row.status,
                message=row.message or None,
                started_at=row.started_at,
                ended_at=row.ended_at,
            )
            for row in sorted(steps, key=lambda item: item.started_at or "")
        ],
        review_status=review_status,
        publication_status=publication_status,
        plan_version=plan_version,
        retry=_retry_view(db, job),
        result=result,
        cancel_requested=bool(job.cancel_requested),
    )


def events_page(db: Session, job: models.Job, after: int = 0, page_size: int = 50) -> dict[str, Any]:
    rows = db.scalars(
        select(models.JobEventRow)
        .where(models.JobEventRow.job_id == job.id, models.JobEventRow.sequence > after)
        .order_by(models.JobEventRow.sequence)
        .limit(page_size + 1)
    ).all()
    has_more = len(rows) > page_size
    visible = rows[:page_size]
    items = [
        JobEvent(
            sequence=row.sequence,
            stage=row.stage,
            status=row.status,
            message=row.message,
            occurred_at=row.occurred_at,
        ).model_dump()
        for row in visible
    ]
    return {
        "items": items,
        "next_sequence": visible[-1].sequence if visible else after,
        "has_more": has_more,
    }

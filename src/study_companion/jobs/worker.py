"""单进程后台任务执行器。

设计要点（06 §6）：

* 一个执行线程，默认顺序执行，避免 SQLite 写竞争与模型并发；
* 领取与终态都用短事务；处理器运行期间**不持有**数据库写事务；
* 处理器返回的结果由调度器在同一事务写回，失败保留真实原因；
* 进程重启后，过期租约任务回到队列或被明确判失败，不会永久 running；
* 提供 ``drain()`` 同步入口，测试与 CLI 可确定性地跑完队列。
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.orm import Session

from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db.session import get_sessionmaker
from study_companion.jobs import service
from study_companion.jobs.registry import JobContext, handler_for

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 0.8


class JobsWorker:
    def __init__(self, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> None:
        self._idle = idle_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- 生命周期 --------------------------------------------------------- #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dsh-jobs", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = run_once()
            except Exception:  # pragma: no cover - 防御性：worker 不应因单次异常退出
                logger.exception("job worker tick failed")
                processed = False
            if not processed:
                self._stop.wait(self._idle)

    # -- 手动 ------------------------------------------------------------- #
    def drain(self, max_jobs: int = 200) -> int:
        done = 0
        while done < max_jobs:
            if not run_once():
                break
            done += 1
        return done


def run_once() -> bool:
    """领取并执行一个任务；返回是否真的处理了任务。"""

    factory = get_sessionmaker()
    db: Session = factory()
    job_id: str | None = None
    try:
        service.recover_expired(db)
        dispatched = dispatch_outbox(db)
        db.commit()
        job = service.claim_next(db)
        if job is None:
            db.commit()
            return bool(dispatched)
        job_id = job.id
        payload = dict(job.payload_json or {})
        scope = dict(job.scope_json or {})
        kind = job.kind
        lease_token = job.lease_token
        db.commit()

        handler = handler_for(kind)
        if handler is None:
            _finalize_failure(
                factory,
                job_id,
                "TASK_NOT_AVAILABLE",
                f"任务类型 {kind} 尚未接入处理器",
                retryable=False,
            )
            return True

        handler_session: Session = factory()
        try:
            job_row = handler_session.get(models.Job, job_id)
            if job_row is None:
                return True
            context = JobContext(job=job_row, session=handler_session)
            result = handler(context)
            handler_session.commit()
        except DomainError as exc:
            handler_session.rollback()
            _finalize_failure(factory, job_id, exc.code, exc.message, retryable=False)
            return True
        except Exception:  # pragma: no cover - 真实故障路径
            handler_session.rollback()
            logger.exception("job handler crashed: %s", kind)
            _finalize_failure(
                factory, job_id, "INTERNAL_ERROR", "任务执行失败，请查看服务端日志", retryable=True
            )
            return True
        finally:
            handler_session.close()

        _finalize_success(factory, job_id, result, lease_token)
        return True
    finally:
        db.close()


def _finalize_success(
    factory, job_id: str, result: dict | None, lease_token: str | None
) -> None:
    db: Session = factory()
    try:
        job = db.get(models.Job, job_id)
        if job is None:
            return
        if job.status != "running":
            return
        if job.cancel_requested:
            job.status = "cancelled"
            job.updated_at = service.now_iso()
            job.lease_token = None
            job.lease_until = None
            service.append_event(
                db, job, stage="cancelled", status="cancelled", message="任务在发布前被取消"
            )
        else:
            service.finalize_success(db, job, result)
        db.commit()
    finally:
        db.close()


def _finalize_failure(
    factory, job_id: str, code: str, message: str, *, retryable: bool
) -> None:
    db: Session = factory()
    try:
        job = db.get(models.Job, job_id)
        if job is None or job.status != "running":
            return
        service.finalize_failure(db, job, code=code, message=message, retryable=retryable)
        db.commit()
    finally:
        db.close()


def dispatch_outbox(db: Session) -> list[str]:
    """把待投递的 outbox 记录转成任务，原子绑定 job_id。"""

    rows = db.query(models.Outbox).filter(models.Outbox.state == "pending").all()
    created: list[str] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        scope = dict(payload.get("scope") or {})
        body = dict(payload.get("payload") or {})
        job = service.enqueue(
            db,
            kind=row.kind,
            scope=scope,
            payload=body,
            dataset_id=scope.get("dataset_id"),
            student_id=scope.get("student_id"),
        )
        row.state = "dispatched"
        row.job_id = job.id
        created.append(job.id)
    return created


_WORKER: JobsWorker | None = None


def get_worker() -> JobsWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = JobsWorker()
    return _WORKER


def start_worker() -> JobsWorker:
    worker = get_worker()
    worker.start()
    return worker


def ensure_worker_started() -> JobsWorker | None:
    """按配置决定是否启动后台线程；测试与 CLI 可关掉它改用 ``drain()``。"""

    from study_companion.config import get_settings

    if not get_settings().jobs_worker_enabled:
        return None
    return start_worker()


def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None


def drain(max_jobs: int = 200) -> int:
    """同步跑完当前队列（测试与 CLI 使用）。"""

    return get_worker().drain(max_jobs=max_jobs)

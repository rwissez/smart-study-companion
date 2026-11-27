"""后台任务包：注册表、服务与执行器。

业务处理器由 ``study_companion.pipeline`` 在应用启动时注册，避免任务包反向依赖
业务模块。
"""

from __future__ import annotations

from study_companion.jobs.registry import JobContext, handler_for, register, registered_kinds
from study_companion.jobs.service import (
    append_event,
    claim_next,
    enqueue,
    enqueue_outbox,
    events_page,
    finalize_failure,
    finalize_success,
    manual_retry,
    recover_expired,
    request_cancel,
    to_detail,
    upsert_step,
)
from study_companion.jobs.worker import JobsWorker, drain, get_worker, start_worker, stop_worker

__all__ = [
    "JobContext",
    "handler_for",
    "register",
    "registered_kinds",
    "append_event",
    "claim_next",
    "enqueue",
    "enqueue_outbox",
    "events_page",
    "finalize_failure",
    "finalize_success",
    "manual_retry",
    "recover_expired",
    "request_cancel",
    "to_detail",
    "upsert_step",
    "JobsWorker",
    "drain",
    "get_worker",
    "start_worker",
    "stop_worker",
]

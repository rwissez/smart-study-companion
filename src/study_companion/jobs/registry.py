"""后台任务注册表。

业务模块在导入时注册处理器，调度器只按 ``kind`` 查表执行，不反向依赖业务模块。
没有处理器的任务不会被伪装成功：会以明确的 ``TASK_NOT_AVAILABLE`` 失败。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from study_companion.db import models


@dataclass(frozen=True)
class JobContext:
    """处理器拿到的不可变上下文：任务行只读，写入通过返回值。"""

    job: models.Job
    session: Session

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.job.payload_json or {})

    @property
    def scope(self) -> dict[str, Any]:
        return dict(self.job.scope_json or {})

    def step(self, step_key: str, label: str) -> None:
        """记录/更新一个步骤的真实状态（提交由调度器负责）。"""

        from study_companion.jobs.service import upsert_step

        upsert_step(self.session, self.job, step_key, label=label, status="running")


JobHandler = Callable[[JobContext], dict[str, Any]]

_HANDLERS: dict[str, JobHandler] = {}


def register(kind: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(handler: JobHandler) -> JobHandler:
        if kind in _HANDLERS and _HANDLERS[kind] is not handler:
            raise RuntimeError(f"job kind already registered: {kind}")
        _HANDLERS[kind] = handler
        return handler

    return decorator


def handler_for(kind: str) -> JobHandler | None:
    return _HANDLERS.get(kind)


def registered_kinds() -> tuple[str, ...]:
    return tuple(sorted(_HANDLERS))

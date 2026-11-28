"""统一任务契约（01 §9.1、§9.5；06 §4.5）。"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from study_companion.contracts.base import Contract, JobStatus
from study_companion.contracts.common import EvidenceRef


class JobScope(Contract):
    # 任务作用域是开放结构：不同 kind 会带自己的定位键（如 export_id、plan_version），
    # 允许附加键以免每加一种任务都要改共享契约。
    model_config = ConfigDict(extra="allow")

    dataset_id: str | None = None
    dataset_family_id: str | None = None
    family_id: str | None = None
    student_id: str | None = None
    class_id: str | None = None
    run_id: str | None = None
    export_id: str | None = None


class JobEvent(Contract):
    sequence: int = Field(ge=1)
    stage: str
    status: str
    message: str = ""
    occurred_at: str = ""


class StepView(Contract):
    step_key: str
    label: str = ""
    status: str = "pending"
    message: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None


class RetryView(Contract):
    root_job_id: str | None = None
    retry_of_job_id: str | None = None
    retry_job_id: str | None = None
    manual_retry_count: int = Field(default=0, ge=0, le=1)
    can_retry: bool = False
    retry_block_reason: str | None = None


class JobDetail(Contract):
    job_id: str
    kind: str
    status: JobStatus
    stage: str | None = None
    scope: JobScope = Field(default_factory=lambda: JobScope())
    created_at: str = ""
    updated_at: str = ""
    result_refs: list[EvidenceRef] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[StepView] = Field(default_factory=list)
    result_ref: str | None = None
    review_status: str | None = None
    publication_status: str | None = None
    plan_version: str | None = None
    retry: RetryView | None = None
    result: dict[str, Any] | None = None
    progress: float | None = None
    cancel_requested: bool = False


class JobEventsPage(Contract):
    items: list[JobEvent] = Field(default_factory=list)
    next_sequence: int = 0
    has_more: bool = False


class RunSummary(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    knowledge_version: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    rule_version: str = ""
    status: str = "succeeded"
    created_at: str = ""
    job_id: str | None = None
    stage: str | None = None
    error: dict[str, Any] | None = None
    analysis_versions: list[str] = Field(default_factory=list)


class Alert(Contract):
    alert_id: str
    kind: str
    status: str
    summary: str
    target_ref: dict[str, Any] | None = None
    student_id: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: str = ""

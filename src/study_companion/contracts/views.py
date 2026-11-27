"""证据解析与正式读取投影（01 §6、§9）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from study_companion.contracts.base import Contract
from study_companion.contracts.catalog import MaterialView
from study_companion.contracts.common import EvidenceRef, SourceLocator
from study_companion.contracts.analysis import Metric


class EvidenceContext(Contract):
    student_id: str | None = None
    class_id: str | None = None
    activity_id: str | None = None
    plan_version: str | None = None


class EvidenceQuery(Contract):
    ref: EvidenceRef
    context: EvidenceContext | None = None


class RubricScoreView(Contract):
    point_id: str
    description: str | None = None
    score: int | None = None
    max_score: int | None = None


class FindingView(Contract):
    finding_id: str
    indicator_id: str
    description: str
    evidence_status: str
    performance: str
    limitations: list[str] = Field(default_factory=list)


class RecordDetail(Contract):
    question: MaterialView
    student_id: str
    assessment_label: str | None = None
    submitted_at: str | None = None
    status: str
    score: int | None = None
    max_score: int | None = None
    final_answer: str | None = None
    solution_steps: str | None = None
    rubric_scores: list[RubricScoreView] = Field(default_factory=list)
    quality_issues: list[dict[str, Any]] = Field(default_factory=list)
    historical_revision: int | None = None
    related_findings: list[FindingView] = Field(default_factory=list)


class MetricDetail(Contract):
    metric: Metric
    calculation_description: str = ""
    evidence_count: int = Field(default=0, ge=0)
    record_refs: list[EvidenceRef] = Field(default_factory=list)
    excluded_count: int = Field(default=0, ge=0)
    limitations: list[str] = Field(default_factory=list)


class CurriculumDetail(Contract):
    title: str
    description: str = ""
    source_locators: list[SourceLocator] = Field(default_factory=list)
    snippet: str | None = None
    basis: str | None = None
    related_refs: list[EvidenceRef] = Field(default_factory=list)


class DerivedDetail(Contract):
    summary: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    version_label: str | None = None


class LearningDetail(Contract):
    event_type: str
    occurred_at: str
    summary: str = ""
    score: int | None = None
    max_score: int | None = None
    related_refs: list[EvidenceRef] = Field(default_factory=list)


DetailType = Literal["record", "metric", "curriculum", "derived", "learning"]


class EvidenceDetail(Contract):
    ref: EvidenceRef
    title: str
    detail_type: DetailType
    detail: dict[str, Any]
    summary: str | None = None
    related_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    visibility: Literal["full", "redacted"] = "full"


class KnowledgeSnapshotView(Contract):
    dataset_id: str
    catalog_version: str
    knowledge_version: str
    parent_version: str | None = None
    status: str = "published"
    points: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    review_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class KnowledgeReviewItem(Contract):
    review_id: str
    object_type: str
    object_id: str
    candidate_version: str
    review_info: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    source_locators: list[SourceLocator] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class KnowledgeCoverageItem(Contract):
    source_type: str
    source_id: str
    status: Literal["verified", "partial", "unmapped", "not_applicable", "needs_review"]
    verified_target_ids: list[str] = Field(default_factory=list)
    missing_reason: str = ""
    review_refs: list[str] = Field(default_factory=list)


class KnowledgeCounts(Contract):
    source_files: int = 0
    total_pages: int = 0
    relevant_pages: int = 0
    usable_relevant_pages: int = 0
    recovered_pages: int = 0
    formal_points: int = 0
    formal_relations: int = 0
    formal_annotations: int = 0
    unresolved_items: int = 0


class CoverageMetric(Contract):
    key: str
    label: str
    numerator: int = Field(default=0, ge=0)
    denominator: int = Field(default=0, ge=0)
    value: float | None = None
    partial_count: int = 0
    not_applicable_count: int = 0
    unknown_count: int = 0
    scope_description: str = ""
    detail_refs: list[str] = Field(default_factory=list)


class AuditCounts(Contract):
    reviewed_objects: int = 0
    first_pass: int = 0
    auto_repaired_pass: int = 0
    human_pass: int = 0
    rejected: int = 0
    needs_review: int = 0
    not_processed: int = 0


class KnowledgeIssueSummary(Contract):
    review_id: str
    object_type: str
    title: str
    reason: str
    source_locators: list[SourceLocator] = Field(default_factory=list)
    status: str = "needs_review"
    blocked_count: int = 0


class KnowledgeQualityReport(Contract):
    dataset_id: str
    catalog_version: str
    knowledge_version: str | None = None
    candidate_version: str = ""
    job_id: str | None = None
    generated_at: str = ""
    counts: KnowledgeCounts = Field(default_factory=KnowledgeCounts)
    coverage: list[CoverageMetric] = Field(default_factory=list)
    audit: AuditCounts = Field(default_factory=AuditCounts)
    issues: list[KnowledgeIssueSummary] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

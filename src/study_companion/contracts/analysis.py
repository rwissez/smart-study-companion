"""B 学情契约：指标、发现、进展、信号与质量报告（01 §5.1、03）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from study_companion.contracts.base import (
    Contract,
    ErrorType,
    EvidenceStatus,
    Freshness,
    GenerationMethod,
    Performance,
    ProgressClassification,
    PublicationStatus,
    ScopeKind,
    StrategyShort,
)
from study_companion.contracts.common import EvidenceRef


class MetricScope(Contract):
    """恰好一个 student_id 或 class_id；粒度以最具体字段识别。"""

    student_id: str | None = None
    class_id: str | None = None
    node_id: str
    indicator_id: str | None = None
    topic_id: str | None = None
    kp_id: str | None = None

    @model_validator(mode="after")
    def one_subject(self):
        if (self.student_id is None) == (self.class_id is None):
            raise ValueError("scope requires exactly one subject")
        return self


class Metric(Contract):
    metric_id: str
    name: str
    value: float | None
    numerator: int | None
    denominator: int | None
    unit: Literal["ratio", "count", "score"]
    scope: MetricScope
    evidence_refs: list[EvidenceRef]
    excluded_event_count: int = 0
    limitations: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus | None = None
    calculation_version: str | None = None


class DiagnosticDetail(Contract):
    summary: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class Finding(Contract):
    finding_id: str
    indicator_id: str
    description: str
    evidence_status: EvidenceStatus
    performance: Performance
    evidence_refs: list[EvidenceRef]
    limitations: list[str] = Field(default_factory=list)
    rubric_focus: list[str] = Field(default_factory=list)
    topic_id: str | None = None
    kp_id: str | None = None
    error_types: list[ErrorType] = Field(default_factory=list)
    diagnostic_details: list[DiagnosticDetail] = Field(default_factory=list)
    generation_method: GenerationMethod = "rule"
    #: 掌握程度（03 §5.4）；未达判定条件时为 unknown，不默认"未掌握"。
    mastery_level: Literal["unknown", "emerging", "developing", "mastered"] | None = None


class ProgressComparison(Contract):
    comparison_id: str
    student_id: str
    baseline_analysis_version: str
    current_analysis_version: str
    scope: MetricScope
    classification: ProgressClassification
    comparable: bool
    #: 比较的指标名（valid_score_ratio 等）；契约显式保留，避免只靠 comparison_id 编码。
    metric_name: str = ""
    baseline_value: float | None = None
    current_value: float | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def incomparable_is_insufficient(self):
        if not self.comparable and self.classification != "insufficient":
            raise ValueError("incomparable comparison must be classified as insufficient")
        return self


class StrategySignal(Contract):
    signal_id: str
    student_id: str
    indicator_id: str
    kp_id: str | None = None
    finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    signal_type: StrategyShort
    limitations: list[str] = Field(default_factory=list)


class DistributionBucket(Contract):
    indicator_id: str
    performance: Performance
    evidence_status: EvidenceStatus
    count: int = Field(ge=0)
    roster_count: int = Field(ge=0)
    kp_id: str | None = None


class AttentionStudent(Contract):
    student_id: str
    finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reason: str
    limitations: list[str] = Field(default_factory=list)


class AnalysisInputRef(Contract):
    dataset_id: str
    catalog_version: str
    knowledge_version: str | None = None
    rule_version: str
    config_hash: str
    event_cutoff: int = Field(ge=0)
    included_event_ids: list[str] = Field(default_factory=list)
    created_at: str = ""


class StudentResult(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    student_id: str
    class_id: str
    node_id: str
    metrics: list[Metric]
    findings: list[Finding]
    limitations: list[str]
    #: 旧记录可确定性迁移生成；新正式写入必须非空（由服务层强制）。
    analysis_version: str = ""
    analysis_input: AnalysisInputRef | None = None
    publication_status: PublicationStatus = "unpublished"
    progress: list[ProgressComparison] = Field(default_factory=list)
    strategy_signals: list[StrategySignal] = Field(default_factory=list)
    summary: str | None = None
    scope_kind: ScopeKind = "assessment"
    dataset_family_id: str | None = None


class ClassResult(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    class_id: str
    node_id: str
    roster_count: int
    observed_student_count: int
    metrics: list[Metric]
    findings: list[Finding]
    limitations: list[str]
    analysis_version: str = ""
    analysis_input: AnalysisInputRef | None = None
    publication_status: PublicationStatus = "unpublished"
    common_findings: list[Finding] = Field(default_factory=list)
    performance_distribution: list[DistributionBucket] = Field(default_factory=list)
    attention_students: list[AttentionStudent] = Field(default_factory=list)
    member_analysis_versions: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    dataset_family_id: str | None = None


class AnalysisConfig(Contract):
    min_valid_distinct_items: int = Field(default=3, ge=1)
    min_reviewed_item_variants: int = Field(default=2, ge=1)
    rule_version: str = "b-rules-1"
    recent_weight: float = Field(default=0.6, ge=0.0, le=1.0)


class RunContext(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    rule_version: str
    dataset_family_id: str | None = None
    knowledge_version: str | None = None


class AnalysisBundle(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    rule_version: str
    config: AnalysisConfig
    student_results: list[StudentResult]
    class_results: list[ClassResult]
    quality_summary: dict[str, int]
    knowledge_version: str | None = None


# --------------------------------------------------------------------------- #
# 质量与报告（03 §15）
# --------------------------------------------------------------------------- #


class QualityCount(Contract):
    key: str
    label: str
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = None
    limitations: list[str] = Field(default_factory=list)


class QualityIssueView(Contract):
    issue_id: str
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    action: str
    record_ids: list[str] = Field(default_factory=list)
    event_id: str | None = None
    affected_metric_names: list[str] = Field(default_factory=list)


class BQualityReport(Contract):
    scope: dict[str, Any]
    node_id: str
    generated_at: str
    rule_version: str
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: list[QualityCount] = Field(default_factory=list)
    issues: list[QualityIssueView] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class StudentResultView(Contract):
    """学生/教师共享的正式学情投影；教师额外字段按授权补齐。"""

    student_id: str
    class_id: str
    node_id: str
    analysis_version: str
    run_id: str
    dataset_id: str
    dataset_family_id: str | None = None
    catalog_version: str
    knowledge_version: str | None = None
    metrics: list[Metric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    progress: list[ProgressComparison] = Field(default_factory=list)
    strategy_signals: list[StrategySignal] = Field(default_factory=list)
    summary: str | None = None
    limitations: list[str] = Field(default_factory=list)
    freshness: Freshness = "current"
    scope_kind: ScopeKind = "assessment"


class ClassResultView(Contract):
    class_id: str
    node_id: str
    analysis_version: str
    run_id: str
    dataset_id: str
    dataset_family_id: str | None = None
    catalog_version: str
    roster_count: int
    observed_student_count: int
    metrics: list[Metric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    common_findings: list[Finding] = Field(default_factory=list)
    performance_distribution: list[DistributionBucket] = Field(default_factory=list)
    attention_students: list[AttentionStudent] = Field(default_factory=list)
    member_analysis_versions: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    limitations: list[str] = Field(default_factory=list)
    freshness: Freshness = "current"


class ComparisonItem(Contract):
    indicator_id: str
    left_value: float | None = None
    right_value: float | None = None
    delta: float | None = None
    left_numerator: int | None = None
    left_denominator: int | None = None
    right_numerator: int | None = None
    right_denominator: int | None = None
    limitations: list[str] = Field(default_factory=list)


class ClassComparisonView(Contract):
    class_ids: list[str]
    node_id: str
    member_analysis_versions: list[dict[str, Any]] = Field(default_factory=list)
    comparable: bool
    items: list[ComparisonItem] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

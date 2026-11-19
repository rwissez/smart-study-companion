"""Versioned data contracts shared by A, B, and C.

The raw CSV payload is deliberately open; all derived business objects are typed.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


RawCell = str | None
RawRow = dict[str, RawCell]


class RawTables(Contract):
    requirements: list[RawRow]
    materials: list[RawRow]
    students: list[RawRow]
    nodes: list[RawRow]
    records: list[RawRow]
    dictionary: list[RawRow]


class QualityIssue(Contract):
    issue_id: str
    code: str
    severity: Literal["info", "warning", "error"]
    record_ids: list[str] = Field(default_factory=list)
    event_id: str | None = None
    message: str
    action: Literal["retain", "exclude_score", "quarantine_event", "block_import"]
    rule_version: str


class Student(Contract):
    student_id: str
    class_id: str
    grade: str | None = None


class Node(Contract):
    node_id: str
    label: str | None = None


class SourceRecord(Contract):
    record_id: str
    table_name: str
    source_row_number: int = Field(ge=1)
    payload: RawRow


class AnswerEvent(Contract):
    event_id: str
    student_id: str
    node_id: str
    material_id: str
    selected_record_id: str | None = None
    status: Literal["graded", "pending", "unanswered"]
    score: int | None = None
    max_score: int | None = None
    disposition: Literal["include", "exclude_score", "quarantine_event"] = "include"


class SourceManifest(Contract):
    dataset_id: str
    table_row_counts: dict[str, int]
    synthetic: bool


class PreparedDataset(Contract):
    students: list[Student]
    nodes: list[Node]
    raw_records: list[SourceRecord]
    selected_events: list[AnswerEvent]
    quality_issues: list[QualityIssue]
    source_manifest: SourceManifest


class SourceLocator(Contract):
    source_file: str
    pdf_page: int | None = None
    printed_page: str | None = None
    verification_status: Literal["verified", "unverified"]


class Topic(Contract):
    topic_id: str
    title: str


class Indicator(Contract):
    indicator_id: str
    topic_id: str
    description: str


class RubricPoint(Contract):
    point_id: str
    description: str
    max_score: int


class Material(Contract):
    material_id: str
    material_type: Literal["question", "card", "textbook"]
    topic_ids: list[str]
    indicator_ids: list[str]
    content: str
    source_locator: SourceLocator
    reference_answer: str | None = None
    reference_explanation: str | None = None
    max_score: int | None = None
    rubric_points: list[RubricPoint] = Field(default_factory=list)
    reviewed_variant_tag: str | None = None


class CatalogLink(Contract):
    source_id: str
    target_id: str
    relation: Literal["supports", "prerequisite"]
    basis: Literal["standard", "textbook", "team_inference"]
    description: str


class Catalog(Contract):
    version: str
    topics: list[Topic]
    indicators: list[Indicator]
    materials: list[Material]
    links: list[CatalogLink]


class EvidenceRef(Contract):
    kind: Literal["record", "metric", "material", "curriculum"]
    id: str
    run_id: str | None = None
    catalog_version: str | None = None

    @model_validator(mode="after")
    def require_owner(self):
        if self.kind in {"record", "metric"} and not self.run_id:
            raise ValueError("record and metric evidence require run_id")
        if self.kind in {"material", "curriculum"} and not self.catalog_version:
            raise ValueError("material and curriculum evidence require catalog_version")
        return self


class MetricScope(Contract):
    student_id: str | None = None
    class_id: str | None = None
    node_id: str
    indicator_id: str | None = None

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


class Finding(Contract):
    finding_id: str
    indicator_id: str
    description: str
    evidence_status: Literal["sufficient", "insufficient", "conflicting"]
    performance: Literal["observed_strength", "needs_practice", "undetermined"]
    evidence_refs: list[EvidenceRef]
    limitations: list[str] = Field(default_factory=list)


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


class AnalysisConfig(Contract):
    min_valid_distinct_items: int = Field(default=3, ge=1)
    min_reviewed_item_variants: int = Field(default=2, ge=1)


class RunContext(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    rule_version: str


class AnalysisBundle(RunContext):
    config: AnalysisConfig
    student_results: list[StudentResult]
    class_results: list[ClassResult]
    quality_summary: dict[str, int]


class Activity(Contract):
    order: int = Field(ge=1)
    material_id: str
    purpose: str
    hint_levels: list[str]
    observation_points: list[str]
    previously_attempted: bool


class AdjustmentCondition(Contract):
    observable_condition: str
    next_action: str


class Recommendation(Contract):
    recommendation_id: str
    indicator_id: str
    finding_ids: list[str]
    goal: str
    reason: str
    activities: list[Activity]
    adjustment_conditions: list[AdjustmentCondition]
    review_status: Literal["draft", "validated", "needs_review"]
    generation_method: Literal["rule", "model"]


class ValidationIssue(Contract):
    code: str
    message: str
    recommendation_id: str | None = None


class RecommendationBundle(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    student_id: str
    node_id: str
    recommendations: list[Recommendation]
    validation_issues: list[ValidationIssue]


class ValidationResult(Contract):
    valid: bool
    issues: list[ValidationIssue]


class ImportConfig(Contract):
    rule_version: str
    quarantine_invalid_scores: bool = True


class RecommendationConfig(Contract):
    max_activities: int = Field(default=3, ge=1)


class MaterialSearch(Contract):
    indicator_ids: list[str] = Field(default_factory=list)
    material_types: list[Literal["question", "card", "textbook"]] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1)


class DomainError(Exception):
    def __init__(self, code: str, message: str, details: list[dict[str, str]] | None = None):
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)

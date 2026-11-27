"""C 学习方案、微课堂与教师编辑契约（01 §5.2、04 §6/§9）。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from study_companion.contracts.base import (
    ActivityKind,
    ActivityState,
    Contract,
    GenerationMethod,
    PlanApplicabilityResult,
    PublicationBasis,
    PublicationStatus,
    PublishPath,
    ReviewResultStatus,
    StrategyType,
)
from study_companion.contracts.common import (
    EvidenceRef,
    MathText,
    ReviewInfo,
    SourceLocator,
    ValidationIssue,
)
from study_companion.contracts.analysis import AnalysisInputRef


ActionGroup = Literal[
    "advance",          # 升阶/推进：证据到位，进入下一步学习
    "fall_back",        # 回退：出错或未接住，降级补台阶
    "escalate_teacher", # 转教师介入
    "wrap_up",          # 收尾记录：亮点归档或如实呈现现状
]


class AdjustmentCondition(Contract):
    observable_condition: str
    next_action: str
    group: ActionGroup = "advance"


def _default_kind() -> ActivityKind:
    return "question"


def _default_state() -> ActivityState:
    return "not_started"


class Activity(Contract):
    """学习活动。旧字段全部保留；新字段用于正式新方案。"""

    order: int = Field(ge=1)
    material_id: str | None = None
    purpose: str = ""
    hint_levels: list[str] = Field(default_factory=list)
    observation_points: list[str] = Field(default_factory=list)
    previously_attempted: bool = False
    activity_id: str = ""
    group_id: str = ""
    knowledge_ids: list[str] = Field(default_factory=list)
    activity_kind: ActivityKind = "question"
    state: ActivityState = "not_started"
    title: str = ""
    expected_outcome: str = ""
    estimate_minutes: int = Field(default=4, ge=1, le=12)
    dependencies: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    exposure_mode: Literal["practice", "independent"] | None = None
    repeat_reason: Literal["error_retry", "independence_check", "spaced_review"] | None = None
    #: 微课堂活动的内容引用；question/study_material 为 null。
    content_ref: "ContentSourceRef | None" = None
    content_version: str | None = None
    #: 学习材料/微课堂的来源说明，学生安全投影。
    source_label: str | None = None

    @model_validator(mode="after")
    def kind_consistency(self) -> "Activity":
        if self.activity_kind == "micro_lesson":
            if self.content_ref is not None and self.content_ref.kind != "personalized":
                raise ValueError("micro_lesson content_ref must be personalized")
        return self


class TaskGroup(Contract):
    group_id: str
    goal: str
    strategy_types: list[StrategyType] = Field(default_factory=list)
    activity_ids: list[str] = Field(default_factory=list)
    order: int = Field(default=1, ge=1)
    primary_strategy: StrategyType | None = None
    auxiliary_strategies: list[StrategyType] = Field(default_factory=list)
    goal_target_key: str | None = None
    expected_outcome: str = ""
    estimated_minutes: int = Field(default=0, ge=0)
    required_activity_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    student_intro: str = ""
    teacher_reason: str = ""


class Recommendation(Contract):
    recommendation_id: str
    indicator_id: str
    finding_ids: list[str]
    goal: str
    reason: str
    student_intro: str = ""
    activities: list[Activity]
    adjustment_conditions: list[AdjustmentCondition]
    review_status: Literal["draft", "validated", "needs_review", "rejected", "human_confirmed"] = "draft"
    generation_method: GenerationMethod = "rule"
    strategy_types: list[StrategyType] = Field(default_factory=list)
    kp_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    review_info: ReviewInfo | None = None
    group_id: str = ""


class RecommendationBundle(Contract):
    run_id: str
    dataset_id: str
    catalog_version: str
    student_id: str
    node_id: str
    recommendations: list[Recommendation]
    validation_issues: list[ValidationIssue]
    plan_version: str = ""
    analysis_version: str = ""
    knowledge_version: str | None = None
    parent_plan_version: str | None = None
    revision: int = Field(default=1, ge=1)
    publication_status: PublicationStatus = "unpublished"
    created_at: str = ""
    task_groups: list[TaskGroup] = Field(default_factory=list)
    dataset_family_id: str | None = None
    publication_basis: PublicationBasis | None = None
    publish_path: PublishPath | None = None
    limitations: list[str] = Field(default_factory=list)
    #: 该方案版本的审核记录；正式读取用它展示 human_confirmed / validated。
    review_info: ReviewInfo | None = None
    draft_hash: str = ""


class Snapshot(Contract):
    """C 生成输入指纹（04 §9 PlanSnapshot）。"""

    dataset_family_id: str
    dataset_id: str
    run_id: str
    student_id: str
    node_id: str
    catalog_version: str
    knowledge_version: str
    analysis_version: str
    event_cutoff: int = Field(ge=0)
    base_plan_version: str | None = None
    config_hash: str = ""
    rule_version: str = "c-rules-1"


class TeachingNeed(Contract):
    target_key: str
    indicator_id: str
    kp_id: str | None = None
    signal_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    evidence_status: str = "insufficient"
    mastery_level: str | None = None
    limitations: list[str] = Field(default_factory=list)
    priority: float = Field(default=0.0, ge=0.0, le=100.0)
    feature_values: list[float] = Field(default_factory=list)
    primary_strategy: StrategyType
    auxiliary_strategies: list[StrategyType] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    waiting_rounds: int = 0


class CPlan(Contract):
    """正式方案（04 §9）：``plan`` 是 RecommendationBundle 投影。"""

    snapshot: Snapshot
    plan: RecommendationBundle
    plan_version: str
    base_plan_version: str | None = None
    revision_id: str | None = None
    review_status: ReviewResultStatus = "draft"
    publication_status: PublicationStatus = "unpublished"
    publication_basis: PublicationBasis | None = None
    publish_path: PublishPath | None = None
    generation_method: GenerationMethod = "rule"
    draft_hash: str = ""
    limitations: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 微课堂固定组件（04 §6.3）
# --------------------------------------------------------------------------- #

ComponentType = Literal["text", "formula", "derivation", "contrast", "number_line", "citation"]


class TextComponent(Contract):
    type: Literal["text"]
    id: str
    heading: str | None = None
    paragraphs: list[str] = Field(min_length=1, max_length=4)


class FormulaComponent(Contract):
    type: Literal["formula"]
    id: str
    expr: str
    caption: str | None = None


class DerivationStep(Contract):
    expr: str
    note: str


class DerivationComponent(Contract):
    type: Literal["derivation"]
    id: str
    mode: Literal["expression", "equation", "inequality"]
    steps: list[DerivationStep] = Field(min_length=2, max_length=6)


class ContrastComponent(Contract):
    type: Literal["contrast"]
    id: str
    before: str
    incorrect: str
    correct: str
    note: str


class IntervalValue(Contract):
    lo: str | None = None
    hi: str | None = None
    lo_closed: bool = False
    hi_closed: bool = False

    @model_validator(mode="after")
    def unbounded_not_closed(self):
        if self.lo is None and self.lo_closed:
            raise ValueError("unbounded lower end cannot be closed")
        if self.hi is None and self.hi_closed:
            raise ValueError("unbounded upper end cannot be closed")
        return self


class NumberLineSet(Contract):
    label: str
    intervals: list[IntervalValue] = Field(default_factory=list, max_length=4)


class NumberLineComponent(Contract):
    type: Literal["number_line"]
    id: str
    min: int = Field(ge=-100, le=100)
    max: int = Field(ge=-100, le=100)
    tick: int = Field(default=1, ge=1, le=10)
    sets: list[NumberLineSet] = Field(min_length=1, max_length=3)
    operation: Literal["none", "intersection"] = "none"
    result_index: int | None = None

    @model_validator(mode="after")
    def intersection_shape(self):
        if self.min >= self.max:
            raise ValueError("number_line min must be less than max")
        if self.operation == "intersection":
            if len(self.sets) != 3 or self.result_index != 2:
                raise ValueError("intersection requires exactly three sets with result_index=2")
        return self


class CitationLabel(Contract):
    label: str
    source_index: int | None = None
    evidence_index: int | None = None

    @model_validator(mode="after")
    def exactly_one_index(self):
        provided = [value for value in (self.source_index, self.evidence_index) if value is not None]
        if len(provided) != 1:
            raise ValueError("citation label requires exactly one of source_index/evidence_index")
        return self


class CitationComponent(Contract):
    type: Literal["citation"]
    id: str
    labels: list[CitationLabel] = Field(min_length=1, max_length=6)


LessonComponent = Annotated[
    TextComponent | FormulaComponent | DerivationComponent | ContrastComponent | NumberLineComponent | CitationComponent,
    Field(discriminator="type"),
]


class OfficialCardRef(Contract):
    kind: Literal["official_card"]
    material_id: str
    catalog_version: str


class VerifiedTextbookRef(Contract):
    kind: Literal["verified_textbook"]
    annotation_id: str
    knowledge_version: str
    locator: SourceLocator


class PersonalizedRef(Contract):
    kind: Literal["personalized"]
    content_id: str
    content_version: str


ContentSourceRef = Annotated[
    OfficialCardRef | VerifiedTextbookRef | PersonalizedRef,
    Field(discriminator="kind"),
]


class LessonDraft(Contract):
    title: str
    goal: str
    estimated_minutes: int = Field(default=3, ge=3, le=6)
    kp_ids: list[str] = Field(min_length=1, max_length=4)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=12)
    source_refs: list[OfficialCardRef | VerifiedTextbookRef] = Field(default_factory=list, max_length=8)
    components: list[LessonComponent] = Field(min_length=1, max_length=12)
    fallback_text: str | None = None

    @model_validator(mode="after")
    def unique_component_ids(self):
        ids = [component.id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("lesson component id must be unique inside the version")
        return self


class LessonVersion(Contract):
    content_id: str
    content_version: str
    schema_version: str = "1.0"
    created_at: str = ""
    creation_method: GenerationMethod = "rule"
    body: LessonDraft
    body_hash: str = ""
    review_info: ReviewInfo | None = None
    publication_status: PublicationStatus = "unpublished"
    origin_plan_version: str | None = None


class MicroLessonView(Contract):
    activity_id: str
    content_id: str
    content_version: str
    schema_version: str
    title: str
    goal: str = ""
    components: list[LessonComponent] = Field(default_factory=list)
    safe_sources: list[dict[str, Any]] = Field(default_factory=list)
    fallback_text: str | None = None
    plan_version: str | None = None
    review_status: ReviewResultStatus = "validated"


# --------------------------------------------------------------------------- #
# 修订、适用性、介入（04 §7—§9）
# --------------------------------------------------------------------------- #


class RevisionChanges(Contract):
    goal: str | None = None
    reason: str | None = None
    activities: list[Activity] | None = None
    adjustment_conditions: list[AdjustmentCondition] | None = None
    student_intro: str | None = None
    lessons: list[dict[str, Any]] | None = None


class RevisionRequest(Contract):
    node_id: str
    recommendation_id: str | None = None
    base_revision: int | None = None
    base_plan_version: str | None = None
    reason: str
    changes: RevisionChanges
    publish: bool = False


class TeacherRevisionPreviewRequest(Contract):
    node_id: str
    base_plan_version: str | None = None
    reason: str
    changes: RevisionChanges


class RevisionSummary(Contract):
    revision_id: str
    revision: int = Field(ge=1)
    plan_version: str = ""
    parent_plan_version: str | None = None
    reason: str = ""
    created_at: str = ""
    review_status: ReviewResultStatus = "draft"
    publication_status: PublicationStatus = "unpublished"
    author_role: str = "teacher"


class RevisionResponse(Contract):
    revision_id: str
    review_status: ReviewResultStatus
    publication_status: PublicationStatus
    job_id: str | None = None
    plan_version: str | None = None


class TeacherPreview(Contract):
    preview_id: str
    draft_hash: str
    warning_digest: str
    warnings: list[ValidationIssue] = Field(default_factory=list)
    expires_at: str


class PlanApplicabilityView(Contract):
    applicability_id: str
    plan_version: str
    origin_analysis_version: str
    current_analysis_version: str
    knowledge_version: str | None = None
    catalog_version: str | None = None
    checked_at: str
    event_cutoff: int = Field(ge=0)
    result: PlanApplicabilityResult
    activity_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PlanView(Contract):
    plan: RecommendationBundle
    plan_version: str
    freshness: Literal["current", "compatible_history", "stale"] = "current"
    limitations: list[str] = Field(default_factory=list)
    applicability: PlanApplicabilityView | None = None
    analysis_version: str = ""


class Intervention(Contract):
    case_id: str
    scope: dict[str, Any] = Field(default_factory=dict)
    target_key: str
    reason_code: str
    status: Literal["open", "resolved"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    trial_ids: list[str] = Field(default_factory=list)
    observed_effects: list[str] = Field(default_factory=list)
    unresolved_question: str = ""
    suggested_actions: list[str] = Field(default_factory=list)
    opened_at: str = ""
    last_cutoff: int = Field(default=0, ge=0)
    resolved_at: str | None = None
    student_id: str = ""
    handling_history: list[dict[str, Any]] = Field(default_factory=list)


class RevisionIntent(Contract):
    goal: str
    reason: str
    requested_changes: list[dict[str, Any]] = Field(default_factory=list)
    teacher_observations: list[dict[str, str]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RevisionSuggestion(Contract):
    group_id: str
    kind: Literal[
        "prefer_candidate", "avoid_candidate", "reorder", "adjust_load", "add_support"
    ]
    activity_keys: list[str] = Field(default_factory=list)
    reason: str
    teacher_intent_keys: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ProposalView(Contract):
    proposal_id: str
    student_id: str
    base_plan_version: str | None = None
    snapshot: Snapshot
    proposal_revision: int = Field(default=1, ge=1)
    draft_hash: str
    changes_summary: list[dict[str, Any]] = Field(default_factory=list)
    candidate_options: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    estimated_minutes: int = 0
    expires_at: str
    status: Literal["ready", "unchanged", "deferred"]


class AgentRevisionSubmission(Contract):
    proposal_id: str
    draft_hash: str
    authorization_ref: str
    student_id: str
    base_plan_version: str | None = None


class StrategyTrial(Contract):
    trial_id: str
    group_id: str
    target_key: str
    strategy: StrategyType
    baseline_analysis_version: str
    started_at: str
    completed_at: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    comparison_id: str | None = None
    status: Literal[
        "planned", "running", "effective", "partial", "ineffective", "inconclusive"
    ] = "planned"
    action_fingerprint: str = ""


class PlanReviewItem(Contract):
    review_id: str
    student_id: str
    plan_version: str
    review_info: ReviewInfo
    summary: str = ""


class CQualityIssue(Contract):
    issue_id: str
    code: str
    severity: Literal["info", "warning", "error"]
    student_id: str | None = None
    plan_version: str | None = None
    group_id: str | None = None
    activity_id: str | None = None
    revision_id: str | None = None
    case_id: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: str = ""
    action: str = ""


class CQualityReport(Contract):
    scope: dict[str, Any] = Field(default_factory=dict)
    window: dict[str, str] = Field(default_factory=dict)
    generated_at: str = ""
    rule_version: str = "c-rules-1"
    counts: dict[str, int] = Field(default_factory=dict)
    issues: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class RecommendationConfig(Contract):
    max_activities: int = Field(default=3, ge=1)
    max_recommendations: int = Field(default=2, ge=1)
    round_minutes: int = Field(default=20, ge=1)
    max_student_activities: int = Field(default=8, ge=1)


NextAction = Literal["advance", "retry_same", "fall_back", "escalate_teacher", "pending"]

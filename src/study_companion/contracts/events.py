"""唯一学习事件账本、尝试与评分契约（01 §7）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from study_companion.contracts.base import Contract
from study_companion.contracts.common import EvidenceRef, RubricScore

EventType = Literal[
    "activity_started",
    "study_material_completed",
    "answer_submitted",
    "grading_completed",
    "hint_requested",
    "assisted_mode_confirmed",
    "agent_help_used",
    "activity_completed",
    "micro_lesson_opened",
    "micro_lesson_completed",
    "group_completed",
    "teacher_review",
    "teacher_note",
    "effectiveness_assessed",
]

EventSource = Literal["student", "teacher", "system"]

#: 每种事件必须出现的 payload 键（01 §7.1）。只校验"存在"，值语义由服务校验。
REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "activity_started": ("activity_kind",),
    "study_material_completed": ("catalog_version", "material_id"),
    "answer_submitted": ("attempt_id", "submission_no"),
    "grading_completed": ("grade_id", "attempt_id", "revision", "grading_status", "max_score"),
    "hint_requested": ("level",),
    "assisted_mode_confirmed": ("confirmation_id", "channel"),
    "agent_help_used": ("conversation_id", "message_id", "activity_id", "help_kind"),
    "activity_completed": ("completion_status",),
    "micro_lesson_opened": ("content_id", "content_version"),
    "micro_lesson_completed": ("content_id", "content_version"),
    "group_completed": ("group_id", "completed_activity_ids"),
    "teacher_review": ("target_type", "target_id", "decision"),
    "teacher_note": ("note",),
    "effectiveness_assessed": ("assessment", "evidence_refs"),
}


class LearningEvent(Contract):
    """追加不可变的唯一事件账本。"""

    event_id: str
    dataset_family_id: str
    dataset_id: str
    run_id: str
    student_id: str
    node_id: str
    plan_version: str | None = None
    group_id: str | None = None
    activity_id: str | None = None
    material_id: str | None = None
    attempt_id: str | None = None
    sequence: int = Field(ge=1)
    occurred_at: str
    source: EventSource
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def required_payload(self):
        missing = [key for key in REQUIRED_PAYLOAD_KEYS[self.event_type] if key not in self.payload]
        if missing:
            raise ValueError(f"{self.event_type} payload missing {missing}")
        if self.event_type.startswith("micro_lesson") and self.material_id:
            raise ValueError("micro lesson events must not carry material_id")
        if self.event_type.startswith("micro_lesson") and not self.activity_id:
            raise ValueError("micro lesson events require activity_id")
        return self


class LearningEventView(Contract):
    event_id: str
    event_type: EventType
    source: EventSource
    occurred_at: str
    sequence: int
    student_id: str
    node_id: str
    plan_version: str | None = None
    group_id: str | None = None
    activity_id: str | None = None
    material_id: str | None = None
    attempt_id: str | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class GradingResult(Contract):
    grade_id: str
    attempt_id: str
    revision: int = Field(ge=1)
    status: Literal["pending", "graded", "needs_review", "unanswered"]
    validation_status: Literal["not_checked", "validated", "rejected"]
    score: int | None = None
    max_score: int
    rubric_scores: list[RubricScore] = Field(default_factory=list)
    method: Literal["rule", "model", "hybrid", "teacher"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    created_at: str = ""
    rule_version: str = ""
    prompt_version: str | None = None

    @model_validator(mode="after")
    def score_bounds(self):
        if self.score is not None:
            if self.score < 0 or self.score > self.max_score:
                raise ValueError("score must be within [0, max_score]")
            if self.rubric_scores and all(point.max_score is not None for point in self.rubric_scores):
                if sum(point.score for point in self.rubric_scores) > self.max_score:
                    raise ValueError("rubric score sum exceeds max_score")
        if self.status in {"pending", "needs_review"} and self.score is not None:
            raise ValueError("pending/needs_review grades must not carry a score")
        return self


class GradingResultView(GradingResult):
    """学生安全投影：未成功评分时不下发 rubric 细节。"""

    feedback: str | None = None


class Attempt(Contract):
    attempt_id: str
    student_id: str
    dataset_id: str
    plan_version: str
    activity_id: str
    material_id: str
    submitted_at: str | None = None
    final_answer: str | None = None
    solution_steps: str | None = None
    status: Literal["in_progress", "submitted", "completed"] = "in_progress"
    latest_grade_id: str | None = None
    assistance_status: Literal["unknown", "independent", "assisted"] = "unknown"
    hint_max_level: int | None = None
    agent_help_count: int | None = None
    submission_no: int = Field(default=0, ge=0)


class AttemptView(Contract):
    attempt_id: str
    student_id: str
    plan_version: str
    activity_id: str
    material_id: str
    status: Literal["in_progress", "submitted", "completed"]
    submitted_at: str | None = None
    final_answer: str | None = None
    solution_steps: str | None = None
    assistance_status: Literal["unknown", "independent", "assisted"] = "unknown"
    hint_max_level: int | None = None
    agent_help_count: int | None = None
    submission_no: int = 0
    latest_grade_id: str | None = None


class AnswerRecordListItem(Contract):
    record_key: str
    origin: Literal["official", "online"]
    node_id: str
    material_id: str
    record_id: str | None = None
    attempt_id: str | None = None
    grade_id: str | None = None
    grading_status: Literal["pending", "graded", "needs_review", "unanswered"]
    score: int | None = None
    max_score: int | None = None
    occurred_at: str | None = None
    evidence_ref: EvidenceRef
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def origin_identity(self):
        if self.origin == "official" and not self.record_id:
            raise ValueError("official records require record_id")
        if self.origin == "online" and not self.attempt_id:
            raise ValueError("online records require attempt_id")
        return self


class HelpDisclosure(Contract):
    disclosure_id: str
    attempt_id: str
    channel: Literal["hint", "agent"]
    message_key: str
    release_order: int = Field(ge=1)
    submission_no: int = Field(ge=0)
    protection_digest: str
    content_digest: str | None = None
    state: Literal["intent", "confirmed", "unknown", "not_sent"]
    intent_at: str
    released_at: str | None = None
    event_id: str | None = None
    reply_ref: str | None = None


# --------------------------------------------------------------------------- #
# 旧兼容投影（main 的学习观察入口）
# --------------------------------------------------------------------------- #

ObservationEventType = Literal[
    "completed_independent",
    "completed_with_hints",
    "step_failed",
    "not_completed",
    "teacher_note",
    "effectiveness_assessed",
]


class ObservationEvent(Contract):
    event_id: str
    run_id: str
    student_id: str
    recommendation_id: str
    activity_order: int
    material_id: str
    event_type: ObservationEventType
    hint_level_used: int | None = None
    failed_step: str | None = None
    teacher_note: str | None = None
    created_at: str = ""


class LearningObservation(Contract):
    """旧观察事件表；作为 C 策略输入投影，不写第二份原始事件。"""

    observation_id: str
    dataset_id: str
    run_id: str
    student_id: str
    node_id: str
    activity_id: str
    material_id: str | None = None
    event_type: Literal[
        "answer_submitted", "hint_requested", "activity_completed", "teacher_review"
    ]
    sequence: int = Field(ge=1)
    hint_level: int | None = Field(default=None, ge=1, le=3)
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    observed_at: str
    source: Literal["student", "teacher", "system"]

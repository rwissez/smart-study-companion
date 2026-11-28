"""公共小对象：包络、引用、分页、审核与数学表达。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from study_companion.contracts.base import (
    Contract,
    ErrorType,
    EvidenceStatus,
    GenerationMethod,
    Performance,
    PublicationBasis,
    PublishPath,
    ReviewMethod,
    ReviewResultStatus,
)

EvidenceKind = Literal[
    "record",
    "metric",
    "material",
    "curriculum",
    "knowledge_point",
    "relation",
    "annotation",
    "rubric_point",
    "learning_event",
    "attempt",
    "grade",
    "finding",
    "progress",
    "recommendation",
    "source_page",
    "lesson",
]

#: 旧 kind 的定位要求（01 §6）：record/metric 需要 run_id，material/curriculum 需要 catalog_version。
_LEGACY_OWNERS = {
    "record": "run_id",
    "metric": "run_id",
    "material": "catalog_version",
    "curriculum": "catalog_version",
}


class Warning_(Contract):
    """包络警告项。模型类名用 ``Warning_`` 以免遮蔽内置 ``Warning``。"""

    code: str
    message: str
    issue_id: str | None = None


class EvidenceRef(Contract):
    """统一证据引用。定位字段由服务端补足并校验，不接受任意对象路径。"""

    kind: EvidenceKind
    id: str
    run_id: str | None = None
    catalog_version: str | None = None
    dataset_id: str | None = None
    knowledge_version: str | None = None
    analysis_version: str | None = None
    plan_version: str | None = None
    material_id: str | None = None
    attempt_id: str | None = None
    curriculum_type: Literal["topic", "indicator", "source"] | None = None

    @model_validator(mode="after")
    def require_owner(self) -> "EvidenceRef":
        if not self.id:
            raise ValueError("evidence id must not be empty")
        owner = _LEGACY_OWNERS.get(self.kind)
        if owner and not getattr(self, owner):
            raise ValueError(f"{self.kind} evidence requires {owner}")
        if self.kind in {"knowledge_point", "relation", "annotation", "lesson"} and not self.knowledge_version:
            raise ValueError(f"{self.kind} evidence requires knowledge_version")
        if self.kind == "rubric_point" and not (self.catalog_version and self.material_id):
            raise ValueError("rubric_point evidence requires catalog_version and material_id")
        if self.kind in {"learning_event", "attempt", "grade"} and not self.dataset_id:
            raise ValueError(f"{self.kind} evidence requires dataset_id")
        if self.kind == "grade" and not self.attempt_id:
            raise ValueError("grade evidence requires attempt_id")
        if self.kind in {"finding", "progress"} and not (self.run_id and self.analysis_version):
            raise ValueError(f"{self.kind} evidence requires run_id and analysis_version")
        if self.kind == "recommendation" and not self.plan_version:
            raise ValueError("recommendation evidence requires plan_version")
        if self.kind == "source_page" and not self.material_id:
            raise ValueError("source_page evidence requires material_id")
        return self

    def key(self) -> str:
        """去重键：同一 kind+id+精确版本才算同一引用。"""

        parts = [
            self.kind,
            self.id,
            self.run_id or "",
            self.catalog_version or "",
            self.dataset_id or "",
            self.knowledge_version or "",
            self.analysis_version or "",
            self.plan_version or "",
            self.material_id or "",
            self.attempt_id or "",
        ]
        return "|".join(parts)


class Page(Contract):
    """分页响应：默认 50，最大 200（01 §9）。"""

    items: list[Any] = Field(default_factory=list)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class ReviewCheck(Contract):
    code: str
    passed: bool
    reason: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ReviewInfo(Contract):
    status: ReviewResultStatus
    method: ReviewMethod
    checks: list[ReviewCheck] = Field(default_factory=list)
    reviewed_at: str | None = None
    reviewer_session_id: str | None = None
    prompt_version: str | None = None
    rule_version: str | None = None
    issues: list["ValidationIssue"] = Field(default_factory=list)
    draft_hash: str | None = None


class ValidationIssue(Contract):
    code: str
    message: str
    recommendation_id: str | None = None
    severity: Literal["info", "warning", "error"] = "warning"
    field: str | None = None
    activity_id: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class MathText(Contract):
    """数学文本：保留规范原值，LaTeX 只作展示，未验证不得作为等价依据。"""

    raw: str
    normalized: str | None = None
    display_latex: str | None = None
    verification_status: Literal["unverified", "verified", "rejected"] = "unverified"


class SourceLocator(Contract):
    """材料出处。``source_file`` 只能是已登记文件或受控来源标记。"""

    source_file: str
    pdf_page: int | None = None
    printed_page: str | None = None
    verification_status: Literal["verified", "unverified"] = "unverified"
    file_id: str | None = None
    source_kind: Literal["standard", "textbook", "competition_material", "other_registered"] | None = None
    chapter: str | None = None
    section: str | None = None
    page_end: int | None = None


class SourceRowRef(Contract):
    dataset_id: str
    table_name: str
    source_row_number: int = Field(ge=1)
    file_id: str | None = None
    record_id: str | None = None


class RubricScore(Contract):
    point_id: str
    score: int
    max_score: int | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ReviewVerdict(Contract):
    """A/B/C 双层审核的单对象结论（02 §9、04 §5）。"""

    object_id: str
    decision: Literal["pass", "repair", "reject", "uncertain"]
    checks: list[ReviewCheck] = Field(default_factory=list)
    corrections: list[dict[str, str]] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class Fact(Contract):
    """Agent 事实表中的一条可核对事实，只能由工具适配器构造。"""

    fact_key: str
    kind: Literal[
        "metric", "finding", "progress", "knowledge", "plan", "operation", "event", "quality", "teacher_observation"
    ]
    subject_ref: str
    statement: str
    value: float | str | None = None
    unit: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    version_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


__all__ = [
    "EvidenceKind",
    "Warning_",
    "EvidenceRef",
    "Page",
    "ReviewCheck",
    "ReviewInfo",
    "ValidationIssue",
    "MathText",
    "SourceLocator",
    "SourceRowRef",
    "RubricScore",
    "ReviewVerdict",
    "Fact",
    "ErrorType",
    "EvidenceStatus",
    "Performance",
    "GenerationMethod",
    "PublicationBasis",
    "PublishPath",
    "ReviewMethod",
    "ReviewResultStatus",
]

"""原始表、导入回执、清洗结果与作答详情（01 §3）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from study_companion.contracts.base import Contract
from study_companion.contracts.common import RubricScore, SourceRowRef


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
    field: str | None = None
    source_refs: list[SourceRowRef] = Field(default_factory=list)
    affected_metric_names: list[str] = Field(default_factory=list)


class Student(Contract):
    student_id: str
    class_id: str
    grade: str | None = None
    student_uid: str | None = None


class Node(Contract):
    node_id: str
    label: str | None = None
    #: 原可空表示"未知"，空数组表示"确定为无题"（01 §4）。
    assessment_date: str | None = None
    topic_ids: list[str] = Field(default_factory=list)
    available_question_ids: list[str] | None = None
    scope_kind: Literal["assessment", "learning_current"] = "assessment"


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
    selected_revision: int | None = None
    detail_ref: str | None = None
    source_event_key: str | None = None
    source_revision: int | None = None


class SourceManifest(Contract):
    dataset_id: str
    table_row_counts: dict[str, int]
    synthetic: bool
    dataset_family_id: str | None = None
    parent_dataset_id: str | None = None
    data_origin: str = "official"


class PreparedDataset(Contract):
    students: list[Student]
    nodes: list[Node]
    raw_records: list[SourceRecord]
    selected_events: list[AnswerEvent]
    quality_issues: list[QualityIssue]
    source_manifest: SourceManifest


class AnswerDetail(Contract):
    """便于跨模块读取的作答详情；不取代 :class:`SourceRecord`。"""

    dataset_id: str
    event_id: str
    record_id: str
    student_id: str
    node_id: str
    material_id: str
    revision: int = Field(ge=1)
    submitted_at: str | None = None
    status: Literal["graded", "pending", "unanswered"]
    score: int | None = None
    max_score: int | None = None
    final_answer: str | None = None
    solution_steps: str | None = None
    rubric_scores: list[RubricScore] = Field(default_factory=list)
    disposition: Literal["include", "exclude_score", "quarantine_event"] = "include"
    source: Literal["official_assessment", "online"] = "official_assessment"
    answer_math: dict[str, Any] | None = None
    steps_math: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 统一导入（01 §3.2）
# --------------------------------------------------------------------------- #

ImportIntent = Literal["new_family", "append_to_family", "auto_detect"]
ImportRole = Literal[
    "requirements",
    "materials",
    "students",
    "nodes",
    "records",
    "dictionary",
    "standard",
    "textbook",
    "unknown",
]


class ImportRequest(Contract):
    intent: ImportIntent
    dataset_family_id: str | None = None
    expected_parent_dataset_id: str | None = None


class ImportFile(Contract):
    file_id: str
    original_name: str
    sha256: str
    byte_size: int = Field(ge=0)
    format: Literal["csv", "xlsx", "pdf"]
    role: ImportRole
    sheet_name: str | None = None
    status: Literal["accepted", "duplicate", "needs_confirmation", "rejected"]
    reason: str | None = None


class ImportReceipt(Contract):
    import_id: str
    dataset_family_id: str | None = None
    dataset_id: str | None = None
    classification_status: Literal["confirmed", "needs_confirmation"]
    input_status: Literal["incomplete", "ready", "blocked"]
    files: list[ImportFile] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    quality_summary: dict[str, int] = Field(default_factory=dict)
    job_id: str | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class FileMapping(Contract):
    file_id: str
    sheet_name: str | None = None
    role: ImportRole


class ImportConfirmRequest(Contract):
    file_mappings: list[FileMapping]
    intent: ImportIntent
    dataset_family_id: str | None = None
    expected_parent_dataset_id: str | None = None


class DatasetSummary(Contract):
    dataset_id: str
    dataset_family_id: str | None = None
    parent_id: str | None = None
    status: str
    created_at: str
    synthetic: bool = False
    display_name: str | None = None
    catalog_version: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    student_count: int = 0
    class_count: int = 0


class ImportConfig(Contract):
    rule_version: str
    quarantine_invalid_scores: bool = True

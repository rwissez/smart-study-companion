"""正式数据库模型。

物理表按 06 §4 分层：

* 原始与目录层：``datasets`` / ``dataset_families`` / ``imports`` / ``file_objects`` /
  ``source_rows`` / ``students`` / ``nodes`` / ``catalogs`` / ``answer_events``。
* 正式成果层：``versions`` / ``formal_heads`` / ``publications`` / ``artifact_edges``。
* 学习闭环层：``learning_events`` / ``attempts`` / ``grades`` / ``activity_state``。
* 调度与审计层：``jobs`` / ``job_events`` / ``outbox`` / ``system_audit``。

旧表结构保持可读：新增列一律可空或有默认值，不覆盖历史行。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from study_companion.db.base import Base


def _json() -> Any:
    # JSON works on every backend; SQLite stores it as text.
    return JSON()


ID = String(128)
KEY = String(256)
HASH = String(64)
TS = String(40)


# --------------------------------------------------------------------------- #
# 原始、目录与历史种子（06 §4.2）
# --------------------------------------------------------------------------- #


class DatasetFamily(Base):
    __tablename__ = "dataset_families"

    family_id: Mapped[str] = mapped_column(ID, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_origin: Mapped[str] = mapped_column(String(32), nullable=False, default="official")
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    active_dataset_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    family_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(HASH, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportRecord(Base):
    __tablename__ = "imports"

    import_id: Mapped[str] = mapped_column(ID, primary_key=True)
    family_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    expected_parent: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    actor_ref: Mapped[str] = mapped_column(ID, nullable=False, default="")
    request_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_status: Mapped[str] = mapped_column(String(32), nullable=False)
    mapping_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    issues_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False, default="")


class FileObject(Base):
    __tablename__ = "file_objects"
    __table_args__ = (UniqueConstraint("sha256", "byte_size", name="uq_file_object_content"),)

    file_id: Mapped[str] = mapped_column(ID, primary_key=True)
    sha256: Mapped[str] = mapped_column(HASH, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(KEY, nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class ImportFileRow(Base):
    __tablename__ = "import_files"
    __table_args__ = (Index("ix_import_files_file", "file_id"),)

    import_id: Mapped[str] = mapped_column(ForeignKey("imports.import_id"), primary_key=True)
    logical_slot: Mapped[str] = mapped_column(KEY, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_objects.file_id"), nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sheet_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="authorized_local")


class DatasetSource(Base):
    __tablename__ = "dataset_sources"
    __table_args__ = (Index("ix_dataset_sources_file", "file_id", "dataset_id"),)

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    source_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file_objects.file_id"), nullable=False)
    origin_import_id: Mapped[str] = mapped_column(ForeignKey("imports.import_id"), nullable=False)
    source_meta_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)


class SourceRow(Base):
    __tablename__ = "source_rows"
    __table_args__ = (
        UniqueConstraint("dataset_id", "table_name", "row_number", name="uq_source_row"),
        Index("ix_source_rows_file", "file_id", "sheet_key", "physical_row"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(32), nullable=False)
    row_number: Mapped[int] = mapped_column(nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("file_objects.file_id"), nullable=True)
    sheet_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    physical_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_hash: Mapped[str | None] = mapped_column(HASH, nullable=True)
    origin_row_id: Mapped[str | None] = mapped_column(ForeignKey("source_rows.id"), nullable=True)


class StudentIdentity(Base):
    __tablename__ = "student_identities"
    __table_args__ = (UniqueConstraint("family_id", "external_id", name="uq_student_identity"),)

    student_uid: Mapped[str] = mapped_column(ID, primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=False)
    external_id: Mapped[str] = mapped_column(ID, nullable=False)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class ClassIdentity(Base):
    __tablename__ = "class_identities"
    __table_args__ = (UniqueConstraint("family_id", "external_id", name="uq_class_identity"),)

    class_uid: Mapped[str] = mapped_column(ID, primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=False)
    external_id: Mapped[str] = mapped_column(ID, nullable=False)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (Index("ix_students_uid", "dataset_id", "student_uid"),)

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    class_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    student_uid: Mapped[str | None] = mapped_column(ID, nullable=True)
    class_uid: Mapped[str | None] = mapped_column(ID, nullable=True)


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (Index("ix_nodes_scope", "dataset_id", "scope_kind"),)

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="assessment")
    scope_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)


class Catalog(Base):
    __tablename__ = "catalogs"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    # Topics and links have no table of their own; they live with the catalog
    # header so that the Catalog contract round-trips without loss.
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    version_id: Mapped[str | None] = mapped_column(ID, nullable=True)


class Indicator(Base):
    __tablename__ = "indicators"

    catalog_version: Mapped[str] = mapped_column(ForeignKey("catalogs.version"), primary_key=True)
    indicator_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class Material(Base):
    __tablename__ = "materials"

    catalog_version: Mapped[str] = mapped_column(ForeignKey("catalogs.version"), primary_key=True)
    material_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class AnswerEvent(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "student_id"],
            ["students.dataset_id", "students.student_id"],
            name="fk_answer_event_student",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "node_id"],
            ["nodes.dataset_id", "nodes.node_id"],
            name="fk_answer_event_node",
        ),
        Index("ix_answer_events_key", "dataset_id", "source_event_key", "source_revision"),
        Index("ix_answer_events_uid", "dataset_id", "student_uid", "node_id"),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_id: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_source_row_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_rows.id"), nullable=True
    )
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, default="include")
    # status / score / max_score come from the AnswerEvent contract and cannot be
    # recovered from the raw row without the per-dataset column mapping, so the
    # judged event body is stored verbatim.
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    raw_row_id: Mapped[str | None] = mapped_column(ForeignKey("source_rows.id"), nullable=True)
    student_uid: Mapped[str | None] = mapped_column(ID, nullable=True)
    source_event_key: Mapped[str | None] = mapped_column(KEY, nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalization_hash: Mapped[str | None] = mapped_column(HASH, nullable=True)


class SourceDisposition(Base):
    __tablename__ = "source_dispositions"
    __table_args__ = (
        Index("ix_source_disposition_key", "dataset_id", "source_event_key", "source_revision"),
    )

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    raw_row_id: Mapped[str] = mapped_column(ForeignKey("source_rows.id"), primary_key=True)
    processing_hash: Mapped[str] = mapped_column(HASH, primary_key=True)
    source_event_key: Mapped[str | None] = mapped_column(KEY, nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    effective_answer_key: Mapped[str | None] = mapped_column(KEY, nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class QualityIssue(Base):
    __tablename__ = "quality_issues"
    __table_args__ = (Index("ix_quality_issues_dataset_version", "dataset_id", "version_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    object_key: Mapped[str | None] = mapped_column(KEY, nullable=True)
    version_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    source_row_id: Mapped[str | None] = mapped_column(ForeignKey("source_rows.id"), nullable=True)
    resolution_ref: Mapped[str | None] = mapped_column(ID, nullable=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (Index("ix_analysis_runs_dataset_created", "dataset_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    catalog_version: Mapped[str] = mapped_column(ForeignKey("catalogs.version"), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(HASH, nullable=True)
    event_cutoff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    import_id: Mapped[str | None] = mapped_column(ForeignKey("imports.import_id"), nullable=True)
    knowledge_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    node_ids_json: Mapped[list[str]] = mapped_column(_json(), nullable=False, default=list)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class RecommendationResult(Base):
    """旧 D 推荐结果表；切新版后只读 legacy。"""

    __tablename__ = "recommendation_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class LearningObservation(Base):
    __tablename__ = "learning_observations"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "student_id", "activity_id", "sequence",
            name="uq_learning_observation_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    material_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    hint_level: Mapped[int | None] = mapped_column(nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)


class DemoSession(Base):
    __tablename__ = "demo_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="demo")


# --------------------------------------------------------------------------- #
# 正式成果与依赖（06 §4.3）
# --------------------------------------------------------------------------- #


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (
        Index("ix_versions_scope", "kind", "dataset_id", "run_id", "node_key", "target_key"),
        Index("ix_versions_job", "job_id"),
    )

    version_id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    node_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    target_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.1")
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    config_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    input_digest: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    body_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("file_objects.file_id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    origin_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="system")


class FormalHead(Base):
    __tablename__ = "formal_heads"
    __table_args__ = (Index("ix_formal_heads_version", "version_id"),)

    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    node_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    target_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.version_id"), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    freshness: Mapped[str] = mapped_column(String(32), nullable=False, default="current")
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)


class ActiveRun(Base):
    __tablename__ = "active_runs"

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    node_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_publication_version"),
        Index("ix_publication_previous", "previous_version_id"),
    )

    publication_id: Mapped[str] = mapped_column(ID, primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.version_id"), nullable=True
    )
    review_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    path: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    published_at: Mapped[str] = mapped_column(TS, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    actor_ref: Mapped[str | None] = mapped_column(ID, nullable=True)


class ArtifactEdge(Base):
    __tablename__ = "artifact_edges"
    __table_args__ = (Index("ix_artifact_edges_dep", "dep_version_id", "role"),)

    version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    dep_version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    object_key: Mapped[str] = mapped_column(KEY, primary_key=True, default="")


class ArtifactUse(Base):
    __tablename__ = "artifact_use"
    __table_args__ = (Index("ix_artifact_use_lookup", "family_id", "input_kind", "input_key"),)

    version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=False)
    input_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    input_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    exact_ref_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    ref_hash: Mapped[str] = mapped_column(HASH, primary_key=True)
    usage: Mapped[str] = mapped_column(String(32), primary_key=True)


class EvidenceIndex(Base):
    __tablename__ = "evidence_index"
    __table_args__ = (Index("ix_evidence_index_object", "kind", "object_id", "version_id"),)

    version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    object_id: Mapped[str] = mapped_column(ID, primary_key=True)
    locator_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="full")


class ClassMember(Base):
    __tablename__ = "class_members"
    __table_args__ = (Index("ix_class_members_student_version", "student_version_id"),)

    class_version_id: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), primary_key=True)
    student_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.version_id"), nullable=True
    )
    member_state: Mapped[str] = mapped_column(String(32), nullable=False, default="available")


class VersionCompatibility(Base):
    __tablename__ = "version_compatibility"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    to_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    scope_hash: Mapped[str] = mapped_column(HASH, primary_key=True)
    policy_hash: Mapped[str] = mapped_column(HASH, primary_key=True)
    scope_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proof_refs_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    checked_at: Mapped[str] = mapped_column(TS, nullable=False)


class PlanApplicability(Base):
    __tablename__ = "plan_applicability"
    __table_args__ = (
        UniqueConstraint(
            "plan_version",
            "target_analysis_version",
            "target_knowledge_version",
            "target_catalog_version",
            "policy_hash",
            "event_cutoff",
            "activity_digest",
            name="uq_plan_applicability",
        ),
    )

    applicability_id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    target_analysis_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    target_knowledge_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    target_catalog_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    policy_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    event_cutoff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activity_digest: Mapped[str] = mapped_column(HASH, nullable=False)
    checked_at: Mapped[str] = mapped_column(TS, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    activity_ids_json: Mapped[list[str]] = mapped_column(_json(), nullable=False, default=list)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    limitations_json: Mapped[list[str]] = mapped_column(_json(), nullable=False, default=list)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_subject", "subject_kind", "subject_key", "created_at"),)

    review_id: Mapped[str] = mapped_column(ID, primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_key: Mapped[str] = mapped_column(ID, nullable=False)
    subject_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    checks_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    issues_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    actor_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class LegacyLink(Base):
    __tablename__ = "legacy_links"

    source_table: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_key: Mapped[str | None] = mapped_column(ID, nullable=True)
    source_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


# --------------------------------------------------------------------------- #
# 学习方案、事件与辅助状态（06 §4.4）
# --------------------------------------------------------------------------- #


class PlanGroup(Base):
    __tablename__ = "plan_groups"
    __table_args__ = (UniqueConstraint("plan_version", "position", name="uq_plan_group_position"),)

    plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    group_id: Mapped[str] = mapped_column(ID, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    goal_key: Mapped[str] = mapped_column(ID, nullable=False, default="")


class PlanActivity(Base):
    __tablename__ = "plan_activities"
    __table_args__ = (
        UniqueConstraint("plan_version", "group_id", "position", name="uq_plan_activity_position"),
        Index("ix_plan_activities_activity", "activity_id", "plan_version"),
    )

    plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ID, primary_key=True)
    group_id: Mapped[str] = mapped_column(ID, nullable=False)
    origin_plan_version: Mapped[str | None] = mapped_column(
        ForeignKey("versions.version_id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    catalog_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    material_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    content_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ActivityStateRow(Base):
    __tablename__ = "activity_state"
    __table_args__ = (Index("ix_activity_state_student", "student_uid", "state"),)

    activity_id: Mapped[str] = mapped_column(ID, primary_key=True)
    origin_plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspension_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)


class AttemptRow(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        Index("ix_attempts_activity_status", "activity_id", "status"),
        Index("ix_attempts_student", "student_uid", "created_at"),
    )

    attempt_id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    activity_id: Mapped[str] = mapped_column(ID, nullable=False)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), nullable=False)
    catalog_version: Mapped[str] = mapped_column(ID, nullable=False, default="")
    material_id: Mapped[str] = mapped_column(ID, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")
    submission_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    help_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assistance_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    answer_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    latest_grade_id: Mapped[str | None] = mapped_column(ID, nullable=True)


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("attempt_id", "revision", name="uq_grade_revision"),)

    grade_id: Mapped[str] = mapped_column(ID, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.attempt_id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_checked")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class LearningEventRow(Base):
    __tablename__ = "learning_events"
    __table_args__ = (
        UniqueConstraint("scope_key", "sequence", name="uq_learning_event_sequence"),
        Index("ix_learning_events_student", "family_id", "student_uid", "offset"),
        Index("ix_learning_events_attempt", "attempt_id", "offset"),
        Index("ix_learning_events_plan_group", "plan_version", "group_id", "event_type"),
    )

    offset: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ID, unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(ForeignKey("dataset_families.family_id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), nullable=False)
    node_id: Mapped[str] = mapped_column(ID, nullable=False)
    plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    group_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    activity_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    material_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.attempt_id"), nullable=True)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    occurred_at: Mapped[str] = mapped_column(TS, nullable=False)
    business_key: Mapped[str] = mapped_column(HASH, nullable=False)


class HelpDisclosureRow(Base):
    __tablename__ = "help_disclosures"
    __table_args__ = (
        UniqueConstraint("attempt_id", "channel", "message_key", name="uq_help_disclosure_message"),
        UniqueConstraint("attempt_id", "release_order", name="uq_help_disclosure_order"),
        Index("ix_help_disclosure_state", "state", "intent_at"),
    )

    disclosure_id: Mapped[str] = mapped_column(ID, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.attempt_id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    message_key: Mapped[str] = mapped_column(KEY, nullable=False)
    release_order: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protection_digest: Mapped[str] = mapped_column(HASH, nullable=False)
    reply_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    content_digest: Mapped[str | None] = mapped_column(HASH, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    intent_at: Mapped[str] = mapped_column(TS, nullable=False)
    released_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    event_id: Mapped[str | None] = mapped_column(ID, nullable=True)


class TeacherIntent(Base):
    __tablename__ = "teacher_intents"
    __table_args__ = (Index("ix_teacher_intents_student", "student_uid", "state"),)

    intent_id: Mapped[str] = mapped_column(ID, primary_key=True)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), nullable=False)
    revision_id: Mapped[str] = mapped_column(ID, nullable=False, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class InterventionRow(Base):
    __tablename__ = "interventions"
    __table_args__ = (Index("ix_interventions_student", "student_uid", "status"),)

    case_id: Mapped[str] = mapped_column(ID, primary_key=True)
    student_uid: Mapped[str] = mapped_column(ForeignKey("student_identities.student_uid"), nullable=False)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    target_key: Mapped[str] = mapped_column(KEY, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    last_cutoff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[str] = mapped_column(TS, nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(TS, nullable=True)


class StrategyTrialRow(Base):
    __tablename__ = "strategy_trials"
    __table_args__ = (
        UniqueConstraint(
            "plan_version", "group_id", "target_key", "strategy", name="uq_strategy_trial"
        ),
        Index("ix_strategy_trials_target", "target_key", "status"),
    )

    trial_id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_version: Mapped[str] = mapped_column(ForeignKey("versions.version_id"), nullable=False)
    group_id: Mapped[str] = mapped_column(ID, nullable=False)
    target_key: Mapped[str] = mapped_column(KEY, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    comparison_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


# --------------------------------------------------------------------------- #
# 任务、Agent 与导出（06 §4.5）
# --------------------------------------------------------------------------- #


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_available", "status", "available_at"),
        Index("ix_jobs_status_lease", "status", "lease_until"),
        Index("ix_jobs_scope", "kind", "scope_key"),
    )

    # 物理列名沿用 D 分支的 ``id``，DTO 字段为 job_id。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    scope_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    input_digest: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    dependency_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    available_at: Mapped[str] = mapped_column(TS, nullable=False, default="")
    lease_token: Mapped[str | None] = mapped_column(ID, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(TS, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(TS, nullable=False, default="")
    retry_of_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    root_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    manual_retry_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_eligible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_block_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    warnings_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    requested_by: Mapped[str | None] = mapped_column(ID, nullable=True)


class JobStep(Base):
    __tablename__ = "job_steps"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    step_key: Mapped[str] = mapped_column(KEY, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoint_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    started_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    ended_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class JobEventRow(Base):
    __tablename__ = "job_events"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[str] = mapped_column(TS, nullable=False)


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (Index("ix_job_dependencies_dep", "depends_on_job_id"),)

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    depends_on_job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_state_available", "state", "available_at"),)

    outbox_id: Mapped[str] = mapped_column(ID, primary_key=True)
    business_key: Mapped[str] = mapped_column(HASH, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    available_at: Mapped[str] = mapped_column(TS, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    scope_session: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    endpoint: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    idem_key: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    request_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    result_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False, default="")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_owner", "owner_ref", "dataset_id", "updated_at"),)

    conversation_id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_ref: Mapped[str] = mapped_column(ID, nullable=False)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    context_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence", name="uq_chat_message_sequence"),)

    message_id: Mapped[str] = mapped_column(ID, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    segments_json: Mapped[list[Any] | None] = mapped_column(_json(), nullable=True)
    request_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class ToolTrace(Base):
    __tablename__ = "tool_traces"
    __table_args__ = (Index("ix_tool_traces_job", "job_id", "step_key"),)

    call_id: Mapped[str] = mapped_column(ID, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    step_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args_digest: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    scope_digest: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    result_refs_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_job", "job_id", "created_at"),)

    call_id: Mapped[str] = mapped_column(ID, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.1")
    input_hash: Mapped[str] = mapped_column(HASH, nullable=False, default="")
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class SystemAudit(Base):
    __tablename__ = "system_audit"
    __table_args__ = (Index("ix_system_audit_scope", "scope_key", "created_at"),)

    audit_id: Mapped[str] = mapped_column(ID, primary_key=True)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    actor_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str] = mapped_column(KEY, nullable=False, default="")
    before_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    after_ref: Mapped[str | None] = mapped_column(ID, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (Index("ix_proposals_scope", "scope_key", "expires_at"),)

    proposal_id: Mapped[str] = mapped_column(ID, primary_key=True)
    proposal_revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner_ref: Mapped[str] = mapped_column(ID, nullable=False)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    base_plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    draft_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    body_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    allowed_candidates_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    changes_summary_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    feedback_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_state_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    expires_at: Mapped[str] = mapped_column(TS, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")


class PlanRevision(Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (Index("ix_plan_revisions_scope", "scope_key", "created_at"),)

    revision_id: Mapped[str] = mapped_column(ID, primary_key=True)
    proposal_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    proposal_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    base_plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    restore_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    result_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class Confirmation(Base):
    __tablename__ = "confirmations"
    __table_args__ = (Index("ix_confirmations_status", "status", "expires_at"),)

    confirmation_id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    message_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    session_ref: Mapped[str] = mapped_column(ID, nullable=False, default="")
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    targets_json: Mapped[list[Any]] = mapped_column(_json(), nullable=False, default=list)
    digest: Mapped[str] = mapped_column(HASH, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[str] = mapped_column(TS, nullable=False)
    decision_at: Mapped[str | None] = mapped_column(TS, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(TS, nullable=False, default="")


class Authorization(Base):
    __tablename__ = "authorizations"
    __table_args__ = (
        UniqueConstraint("confirmation_id", "scope_key", "proposal_id", name="uq_authorization"),
    )

    authorization_ref: Mapped[str] = mapped_column(ID, primary_key=True)
    confirmation_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    owner_ref: Mapped[str] = mapped_column(ID, nullable=False)
    request_message_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    proposal_id: Mapped[str] = mapped_column(ID, nullable=False)
    proposal_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draft_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    base_plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)


class AgentOperation(Base):
    __tablename__ = "agent_operations"
    __table_args__ = (UniqueConstraint("authorization_ref", "student_uid", name="uq_agent_operation"),)

    operation_id: Mapped[str] = mapped_column(ID, primary_key=True)
    authorization_ref: Mapped[str] = mapped_column(ForeignKey("authorizations.authorization_ref"), nullable=False)
    student_uid: Mapped[str] = mapped_column(ID, nullable=False)
    c_job_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    new_plan_version: Mapped[str | None] = mapped_column(ID, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)


class Export(Base):
    __tablename__ = "exports"
    __table_args__ = (Index("ix_exports_scope", "scope_key", "created_at"),)

    export_id: Mapped[str] = mapped_column(ID, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ID, nullable=False, unique=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    scope_key: Mapped[str] = mapped_column(KEY, nullable=False)
    actor_ref: Mapped[str] = mapped_column(ID, nullable=False, default="")
    selection_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    selection_hash: Mapped[str] = mapped_column(HASH, nullable=False)
    event_cutoff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(KEY, nullable=True, unique=True)
    archive_hash: Mapped[str | None] = mapped_column(HASH, nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_report_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[str] = mapped_column(TS, nullable=False)
    updated_at: Mapped[str] = mapped_column(TS, nullable=False)

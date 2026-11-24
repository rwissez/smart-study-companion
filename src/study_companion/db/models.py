"""ORM tables created by the A0 migration.

A0 creates the twelve tables on the import -> catalog -> analysis -> session
vertical only. Recommendation revisions, conversations, jobs, idempotency keys
and exports arrive in a later migration (A1) so that A0 stays reviewable.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from study_companion.db.base import Base


def _json() -> Any:
    # JSON works on every backend; SQLite stores it as text.
    return JSON()


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class SourceRow(Base):
    __tablename__ = "source_rows"
    __table_args__ = (UniqueConstraint("dataset_id", "table_name", "row_number", name="uq_source_row"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(32), nullable=False)
    row_number: Mapped[int] = mapped_column(nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class Student(Base):
    __tablename__ = "students"

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    class_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Node(Base):
    __tablename__ = "nodes"

    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class Catalog(Base):
    __tablename__ = "catalogs"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    # Topics and links have no table of their own; they live with the catalog
    # header so that the Catalog contract round-trips without loss.
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False, default=dict)


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


class QualityIssue(Base):
    __tablename__ = "quality_issues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    catalog_version: Mapped[str] = mapped_column(ForeignKey("catalogs.version"), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(_json(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(_json(), nullable=True)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
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

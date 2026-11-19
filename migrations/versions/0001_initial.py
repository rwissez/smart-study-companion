"""A0 initial schema: import, catalog, analysis and demo sessions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["datasets.id"], name="fk_datasets_parent"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "source_rows",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("table_name", sa.String(length=32), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_source_rows_dataset"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "table_name", "row_number", name="uq_source_row"),
    )
    op.create_index("ix_source_rows_dataset_id", "source_rows", ["dataset_id"])

    op.create_table(
        "students",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("class_id", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_students_dataset"),
        sa.PrimaryKeyConstraint("dataset_id", "student_id"),
    )
    op.create_index("ix_students_class_id", "students", ["class_id"])

    op.create_table(
        "nodes",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_nodes_dataset"),
        sa.PrimaryKeyConstraint("dataset_id", "node_id"),
    )

    op.create_table(
        "catalogs",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_catalogs_dataset"),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_index("ix_catalogs_dataset_id", "catalogs", ["dataset_id"])

    op.create_table(
        "indicators",
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("indicator_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_version"], ["catalogs.version"], name="fk_indicators_catalog"
        ),
        sa.PrimaryKeyConstraint("catalog_version", "indicator_id"),
    )

    op.create_table(
        "materials",
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("material_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_version"], ["catalogs.version"], name="fk_materials_catalog"
        ),
        sa.PrimaryKeyConstraint("catalog_version", "material_id"),
    )

    op.create_table(
        "answer_events",
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("material_id", sa.String(length=64), nullable=False),
        sa.Column("selected_source_row_id", sa.String(length=64), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_answer_events_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "student_id"],
            ["students.dataset_id", "students.student_id"],
            name="fk_answer_event_student",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "node_id"],
            ["nodes.dataset_id", "nodes.node_id"],
            name="fk_answer_event_node",
        ),
        sa.ForeignKeyConstraint(
            ["selected_source_row_id"], ["source_rows.id"], name="fk_answer_event_source_row"
        ),
        sa.PrimaryKeyConstraint("dataset_id", "event_id"),
    )
    op.create_index("ix_answer_events_student_id", "answer_events", ["student_id"])
    op.create_index("ix_answer_events_node_id", "answer_events", ["node_id"])

    op.create_table(
        "quality_issues",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_quality_issues_dataset"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_issues_dataset_id", "quality_issues", ["dataset_id"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_analysis_runs_dataset"
        ),
        sa.ForeignKeyConstraint(
            ["catalog_version"], ["catalogs.version"], name="fk_analysis_runs_catalog"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_runs_dataset_id", "analysis_runs", ["dataset_id"])

    op.create_table(
        "analysis_results",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], name="fk_analysis_results_run"),
        sa.PrimaryKeyConstraint("run_id", "subject_type", "subject_id", "node_id"),
    )

    op.create_table(
        "demo_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=True),
        sa.Column("student_id", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_demo_sessions_dataset"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_demo_sessions_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("demo_sessions")
    op.drop_table("analysis_results")
    op.drop_index("ix_analysis_runs_dataset_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index("ix_quality_issues_dataset_id", table_name="quality_issues")
    op.drop_table("quality_issues")
    op.drop_index("ix_answer_events_node_id", table_name="answer_events")
    op.drop_index("ix_answer_events_student_id", table_name="answer_events")
    op.drop_table("answer_events")
    op.drop_table("materials")
    op.drop_table("indicators")
    op.drop_index("ix_catalogs_dataset_id", table_name="catalogs")
    op.drop_table("catalogs")
    op.drop_table("nodes")
    op.drop_index("ix_students_class_id", table_name="students")
    op.drop_table("students")
    op.drop_index("ix_source_rows_dataset_id", table_name="source_rows")
    op.drop_table("source_rows")
    op.drop_table("datasets")

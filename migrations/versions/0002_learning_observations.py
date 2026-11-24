"""Add post-publication learning observation events.

Revision ID: 0002_learning_observations
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_learning_observations"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_observations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("activity_id", sa.String(length=64), nullable=False),
        sa.Column("material_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("hint_level", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], name="fk_observations_dataset"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], name="fk_observations_run"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "student_id", "activity_id", "sequence",
            name="uq_learning_observation_sequence",
        ),
    )
    op.create_index("ix_learning_observations_dataset_id", "learning_observations", ["dataset_id"])
    op.create_index("ix_learning_observations_run_id", "learning_observations", ["run_id"])
    op.create_index("ix_learning_observations_student_id", "learning_observations", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_observations_student_id", table_name="learning_observations")
    op.drop_index("ix_learning_observations_run_id", table_name="learning_observations")
    op.drop_index("ix_learning_observations_dataset_id", table_name="learning_observations")
    op.drop_table("learning_observations")

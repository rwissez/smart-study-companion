"""Add rule recommendation results and local jobs.

Revision ID: 0002_local_results
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_local_results"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recommendation_results",
        sa.Column("run_id", sa.String(64), sa.ForeignKey("analysis_runs.id"), primary_key=True),
        sa.Column("student_id", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(64), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("student_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_table("jobs")
    op.drop_table("recommendation_results")

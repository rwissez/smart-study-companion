"""Persist idempotency keys for local data imports.

Revision ID: 0003_idempotency
Revises: 0002_local_results
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_idempotency"
down_revision = "0002_local_results"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "idempotency_records",
        sa.Column("operation", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("idempotency_records")

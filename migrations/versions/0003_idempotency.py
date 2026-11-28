"""Persist idempotency keys for local data imports.

从 D 集成分支（64bd53a）原样迁入。0005 会把键结构升级为
``(scope_session, endpoint, idem_key)``，保留旧行到 ``legacy`` 作用域。

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

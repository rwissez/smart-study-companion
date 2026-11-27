"""Merge the main and D migration branches into one head.

main 分支以 ``0002_learning_observations`` 为头，D 分支以 ``0003_idempotency`` 为头，
两者共同祖先为 ``0001_initial``。本迁移只做合并，不建表；正式交付 schema 在
``0005_delivery_schema`` 建立。

Revision ID: 0004_merge_delivery
Revises: 0002_learning_observations, 0003_idempotency
"""

from collections.abc import Sequence

revision: str = "0004_merge_delivery"
down_revision: tuple[str, str] = ("0002_learning_observations", "0003_idempotency")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """两条已发布历史在此汇合，不需要数据变更。"""


def downgrade() -> None:
    """拆分回两个头。"""

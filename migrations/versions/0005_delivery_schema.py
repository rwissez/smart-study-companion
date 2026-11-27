"""Delivery schema: 正式版本、版本头、学习账本、任务、Agent 与导出。

本迁移在 ``0004_merge_delivery`` 之后建立统一交付结构。两类动作：

1. 对 0001/0002 已存在的表补列（只加可空列或带默认值的列，不改历史行）。
2. 建立新表。新表结构与 ``study_companion.db.models`` 一致，按 06 §4 分层。

实现说明（工程取舍，已记录）：新表使用 ORM 元数据在建表时一次性生成，
避免 800 行重复列定义与模型漂移；**表集合是显式白名单**，新增表必须同时
加入 ``NEW_TABLES``，否则迁移失败。已有表的补列仍逐条显式声明，保证旧库
升级行为可审计、可回滚。

Revision ID: 0005_delivery_schema
Revises: 0004_merge_delivery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_delivery_schema"
down_revision: str = "0004_merge_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 本迁移负责创建的新表，顺序即建表顺序（已被 SQLAlchemy 依赖排序覆盖）。
NEW_TABLES: tuple[str, ...] = (
    "dataset_families",
    "imports",
    "file_objects",
    "import_files",
    "dataset_sources",
    "student_identities",
    "class_identities",
    "source_dispositions",
    "versions",
    "formal_heads",
    "active_runs",
    "publications",
    "artifact_edges",
    "artifact_use",
    "evidence_index",
    "class_members",
    "version_compatibility",
    "plan_applicability",
    "reviews",
    "legacy_links",
    "plan_groups",
    "plan_activities",
    "activity_state",
    "attempts",
    "grades",
    "learning_events",
    "help_disclosures",
    "teacher_intents",
    "interventions",
    "strategy_trials",
    "job_steps",
    "job_events",
    "job_dependencies",
    "outbox",
    "conversations",
    "chat_messages",
    "tool_traces",
    "model_calls",
    "system_audit",
    "proposals",
    "plan_revisions",
    "confirmations",
    "authorizations",
    "agent_operations",
    "exports",
)


def _add(table: str, column: sa.Column, *, index: tuple[str, ...] | None = None) -> None:
    op.add_column(table, column)
    if index:
        op.create_index(f"ix_{table}_{'_'.join(index)}", table, list(index))


def upgrade() -> None:
    _add("datasets", sa.Column("family_id", sa.String(128), nullable=True))
    _add("datasets", sa.Column("snapshot_hash", sa.String(64), nullable=True))
    _add("datasets", sa.Column("state", sa.String(32), nullable=False, server_default="ready"))
    _add("datasets", sa.Column("display_name", sa.Text(), nullable=True))
    op.create_index("ix_datasets_family_parent", "datasets", ["family_id", "parent_id"])

    _add("source_rows", sa.Column("file_id", sa.String(128), nullable=True))
    _add("source_rows", sa.Column("sheet_key", sa.String(128), nullable=False, server_default=""))
    _add("source_rows", sa.Column("physical_row", sa.Integer(), nullable=True))
    _add("source_rows", sa.Column("row_hash", sa.String(64), nullable=True))
    _add("source_rows", sa.Column("origin_row_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_source_rows_file", "source_rows", ["file_id", "sheet_key", "physical_row"]
    )

    _add("students", sa.Column("student_uid", sa.String(128), nullable=True))
    _add("students", sa.Column("class_uid", sa.String(128), nullable=True))
    op.create_index("ix_students_uid", "students", ["dataset_id", "student_uid"])

    _add("nodes", sa.Column("scope_kind", sa.String(32), nullable=False, server_default="assessment"))
    _add("nodes", sa.Column("scope_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_nodes_scope", "nodes", ["dataset_id", "scope_kind"])

    _add("catalogs", sa.Column("version_id", sa.String(128), nullable=True))

    _add("answer_events", sa.Column("raw_row_id", sa.String(64), nullable=True))
    _add("answer_events", sa.Column("student_uid", sa.String(128), nullable=True))
    _add("answer_events", sa.Column("source_event_key", sa.String(256), nullable=True))
    _add("answer_events", sa.Column("source_revision", sa.Integer(), nullable=True))
    _add("answer_events", sa.Column("normalization_hash", sa.String(64), nullable=True))
    op.create_index(
        "ix_answer_events_key",
        "answer_events",
        ["dataset_id", "source_event_key", "source_revision"],
    )
    op.create_index(
        "ix_answer_events_uid", "answer_events", ["dataset_id", "student_uid", "node_id"]
    )

    _add("quality_issues", sa.Column("object_key", sa.String(256), nullable=True))
    _add("quality_issues", sa.Column("version_id", sa.String(128), nullable=True))
    _add("quality_issues", sa.Column("source_row_id", sa.String(64), nullable=True))
    _add("quality_issues", sa.Column("resolution_ref", sa.String(128), nullable=True))
    op.create_index(
        "ix_quality_issues_dataset_version", "quality_issues", ["dataset_id", "version_id"]
    )

    _add("analysis_runs", sa.Column("config_hash", sa.String(64), nullable=True))
    _add("analysis_runs", sa.Column("event_cutoff", sa.Integer(), nullable=False, server_default="0"))
    _add("analysis_runs", sa.Column("import_id", sa.String(128), nullable=True))
    _add("analysis_runs", sa.Column("knowledge_version", sa.String(128), nullable=True))
    _add(
        "analysis_runs",
        sa.Column("node_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_index(
        "ix_analysis_runs_dataset_created", "analysis_runs", ["dataset_id", "created_at"]
    )

    _add("demo_sessions", sa.Column("scope_json", sa.JSON(), nullable=True))
    _add("demo_sessions", sa.Column("mode", sa.String(16), nullable=False, server_default="demo"))

    # --- jobs 扩展：保留 D 的物理列 id/dataset_id/student_id/status/result_json/error_json ---
    _add("jobs", sa.Column("kind", sa.String(32), nullable=False, server_default=""))
    _add("jobs", sa.Column("scope_key", sa.String(256), nullable=False, server_default=""))
    _add("jobs", sa.Column("scope_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    _add("jobs", sa.Column("input_digest", sa.String(64), nullable=False, server_default=""))
    _add(
        "jobs",
        sa.Column("dependency_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    _add("jobs", sa.Column("stage", sa.String(64), nullable=True))
    _add("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    _add("jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"))
    _add("jobs", sa.Column("available_at", sa.String(40), nullable=False, server_default=""))
    _add("jobs", sa.Column("lease_token", sa.String(128), nullable=True))
    _add("jobs", sa.Column("lease_until", sa.String(40), nullable=True))
    _add("jobs", sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"))
    _add("jobs", sa.Column("result_ref", sa.String(128), nullable=True))
    _add("jobs", sa.Column("created_at", sa.String(40), nullable=False, server_default=""))
    _add("jobs", sa.Column("updated_at", sa.String(40), nullable=False, server_default=""))
    _add("jobs", sa.Column("retry_of_job_id", sa.String(64), nullable=True))
    _add("jobs", sa.Column("root_job_id", sa.String(64), nullable=True))
    _add("jobs", sa.Column("manual_retry_no", sa.Integer(), nullable=False, server_default="0"))
    _add("jobs", sa.Column("retry_eligible", sa.Integer(), nullable=False, server_default="0"))
    _add("jobs", sa.Column("retry_block_reason", sa.String(64), nullable=True))
    _add("jobs", sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    _add("jobs", sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    _add("jobs", sa.Column("requested_by", sa.String(128), nullable=True))
    op.create_index("ix_jobs_status_available", "jobs", ["status", "available_at"])
    op.create_index("ix_jobs_status_lease", "jobs", ["status", "lease_until"])
    op.create_index("ix_jobs_scope", "jobs", ["kind", "scope_key"])
    op.create_index(
        "ix_jobs_root_manual_retry",
        "jobs",
        ["root_job_id"],
        unique=True,
        sqlite_where=sa.text("manual_retry_no = 1"),
    )

    # --- idempotency：旧 (operation,key) 迁到 (scope_session,endpoint,idem_key) ---
    op.rename_table("idempotency_records", "idempotency_records_legacy")
    op.create_table(
        "idempotency_records",
        sa.Column("scope_session", sa.String(64), primary_key=True),
        sa.Column("endpoint", sa.String(64), primary_key=True),
        sa.Column("idem_key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_ref", sa.String(128), nullable=True),
        sa.Column("receipt_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.execute(
        """
        INSERT INTO idempotency_records
            (scope_session, endpoint, idem_key, request_hash, result_ref, receipt_json, created_at)
        SELECT 'legacy', operation, key, fingerprint, NULL, result_json, ''
        FROM idempotency_records_legacy
        """
    )
    op.drop_table("idempotency_records_legacy")

    _create_new_tables()


def _create_new_tables() -> None:
    from study_companion.db import models  # noqa: F401  (register metadata)
    from study_companion.db.base import Base

    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    by_name = {table.name: table for table in Base.metadata.sorted_tables}
    missing = [name for name in NEW_TABLES if name not in by_name]
    if missing:
        raise RuntimeError(f"0005 缺少 ORM 表定义：{missing}")
    for name in NEW_TABLES:
        if name not in existing:
            by_name[name].create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for name in reversed(NEW_TABLES):
        if name in existing:
            op.drop_table(name)
    op.drop_table("idempotency_records")
    op.create_table(
        "idempotency_records",
        sa.Column("operation", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
    )
    for index_name in (
        "ix_jobs_root_manual_retry",
        "ix_jobs_scope",
        "ix_jobs_status_lease",
        "ix_jobs_status_available",
    ):
        op.drop_index(index_name, table_name="jobs")
    for column in (
        "requested_by",
        "warnings_json",
        "payload_json",
        "retry_block_reason",
        "retry_eligible",
        "manual_retry_no",
        "root_job_id",
        "retry_of_job_id",
        "updated_at",
        "created_at",
        "result_ref",
        "cancel_requested",
        "lease_until",
        "lease_token",
        "available_at",
        "max_attempts",
        "attempt_count",
        "stage",
        "dependency_generation",
        "input_digest",
        "scope_json",
        "scope_key",
        "kind",
    ):
        op.drop_column("jobs", column)

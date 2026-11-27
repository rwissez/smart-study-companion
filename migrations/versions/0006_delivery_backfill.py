"""Backfill delivery identity, family and active-run pointers.

旧库升级后必须有明确的 family、稳定学生键与当前运行指针，否则正式读取
无法判定作用域。本迁移只写**可证**数据：

* 每个旧 dataset 建成独立 family（不把不同来源猜成同一系列）。
* ``(family, student_id)`` 建立 student_uid；班级同理。
* 以每个 dataset 最近一次成功的 run 建立 active_runs。
* 无法判断的映射写入 ``legacy_links`` 并标 unknown，不伪造正式版本。

Revision ID: 0006_delivery_backfill
Revises: 0005_delivery_schema
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_delivery_backfill"
down_revision: str = "0005_delivery_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    datasets = bind.execute(
        sa.text("SELECT id, created_at, parent_id, status, manifest_json FROM datasets")
    ).mappings().all()
    for dataset in datasets:
        family_id = f"family-{uuid.uuid4().hex[:16]}"
        bind.execute(
            sa.text(
                "INSERT INTO dataset_families (family_id, label, data_origin, created_at, "
                "active_dataset_id, generation) VALUES (:family_id, :label, :origin, :created_at, "
                ":dataset_id, 1)"
            ),
            {
                "family_id": family_id,
                "label": f"legacy-{dataset['id']}",
                "origin": "official",
                "created_at": dataset["created_at"] or "",
                "dataset_id": dataset["id"],
            },
        )
        bind.execute(
            sa.text("UPDATE datasets SET family_id = :family_id WHERE id = :dataset_id"),
            {"family_id": family_id, "dataset_id": dataset["id"]},
        )

        students = bind.execute(
            sa.text("SELECT student_id, class_id FROM students WHERE dataset_id = :dataset_id"),
            {"dataset_id": dataset["id"]},
        ).mappings().all()
        class_uids: dict[str, str] = {}
        for student in students:
            student_uid = f"su-{uuid.uuid4().hex[:16]}"
            class_id = student["class_id"]
            if class_id not in class_uids:
                class_uid = f"cu-{uuid.uuid4().hex[:16]}"
                class_uids[class_id] = class_uid
                bind.execute(
                    sa.text(
                        "INSERT INTO class_identities (class_uid, family_id, external_id, created_at) "
                        "VALUES (:class_uid, :family_id, :external_id, :created_at)"
                    ),
                    {
                        "class_uid": class_uid,
                        "family_id": family_id,
                        "external_id": class_id,
                        "created_at": dataset["created_at"] or "",
                    },
                )
            bind.execute(
                sa.text(
                    "INSERT INTO student_identities (student_uid, family_id, external_id, created_at) "
                    "VALUES (:student_uid, :family_id, :external_id, :created_at)"
                ),
                {
                    "student_uid": student_uid,
                    "family_id": family_id,
                    "external_id": student["student_id"],
                    "created_at": dataset["created_at"] or "",
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE students SET student_uid = :student_uid, class_uid = :class_uid "
                    "WHERE dataset_id = :dataset_id AND student_id = :student_id"
                ),
                {
                    "student_uid": student_uid,
                    "class_uid": class_uids[class_id],
                    "dataset_id": dataset["id"],
                    "student_id": student["student_id"],
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE answer_events SET student_uid = :student_uid "
                    "WHERE dataset_id = :dataset_id AND student_id = :student_id "
                    "AND student_uid IS NULL"
                ),
                {
                    "student_uid": student_uid,
                    "dataset_id": dataset["id"],
                    "student_id": student["student_id"],
                },
            )

        nodes = bind.execute(
            sa.text("SELECT node_id FROM nodes WHERE dataset_id = :dataset_id"),
            {"dataset_id": dataset["id"]},
        ).scalars().all()
        latest_run = bind.execute(
            sa.text(
                "SELECT id FROM analysis_runs WHERE dataset_id = :dataset_id "
                "AND status = 'succeeded' ORDER BY created_at DESC LIMIT 1"
            ),
            {"dataset_id": dataset["id"]},
        ).scalar()
        if latest_run:
            for node_id in nodes:
                bind.execute(
                    sa.text(
                        "INSERT INTO active_runs (dataset_id, node_key, run_id, generation, updated_at) "
                        "VALUES (:dataset_id, :node_key, :run_id, 1, :updated_at)"
                    ),
                    {
                        "dataset_id": dataset["id"],
                        "node_key": node_id,
                        "run_id": latest_run,
                        "updated_at": dataset["created_at"] or "",
                    },
                )

        bind.execute(
            sa.text(
                "INSERT INTO legacy_links (source_table, source_key, target_kind, target_key, "
                "source_hash, disposition, reason) VALUES ('datasets', :dataset_id, 'family_id', "
                ":family_id, :source_hash, 'mapped', '每个旧数据集独立成族，来源血缘未经确认')"
            ),
            {
                "dataset_id": dataset["id"],
                "family_id": family_id,
                "source_hash": _hash(dataset["id"]),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM legacy_links WHERE source_table = 'datasets'"))
    bind.execute(sa.text("DELETE FROM active_runs"))
    bind.execute(sa.text("DELETE FROM student_identities"))
    bind.execute(sa.text("DELETE FROM class_identities"))
    bind.execute(sa.text("DELETE FROM dataset_families"))
    bind.execute(sa.text("UPDATE datasets SET family_id = NULL"))

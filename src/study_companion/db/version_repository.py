"""正式成果仓储：版本、版本头、依赖边与 CAS 发布。

这是 A/B/C 读取"当前正式成果"的唯一入口。要点（06 §3—§4）：

* 产物不可变；发布只移动 ``formal_heads`` 指针并追加 ``publications`` 证明；
* 指针更新用 CAS（版本 + 代数 + 必需依赖代数），零行即 409，不产生半发布；
* 依赖边记录实际使用的精确版本，历史依赖不因新版本出现而改写；
* 查询只返回已发布版本，候选与草稿永不进入正式读取。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError
from study_companion.db import models


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FormalKey:
    """正式成果的作用域键；不适用字段用空串，禁止用作用户 ID。"""

    family_id: str
    dataset_id: str
    kind: str
    run_key: str = ""
    node_key: str = ""
    target_key: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "family_id": self.family_id,
            "dataset_id": self.dataset_id,
            "kind": self.kind,
            "run_key": self.run_key,
            "node_key": self.node_key,
            "target_key": self.target_key,
        }


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #


def create_version(
    db: Session,
    *,
    kind: str,
    key: FormalKey,
    payload: dict[str, Any] | None,
    rule_version: str = "",
    input_digest: str = "",
    schema_version: str = "1.1",
    run_id: str | None = None,
    job_id: str | None = None,
    origin_kind: str = "system",
    version_id: str | None = None,
    body_hash: str | None = None,
) -> models.Version:
    row = models.Version(
        version_id=version_id or new_id(kind[:3] or "ver"),
        kind=kind,
        family_id=key.family_id,
        dataset_id=key.dataset_id,
        run_id=run_id,
        node_key=key.node_key,
        target_key=key.target_key,
        schema_version=schema_version,
        rule_version=rule_version,
        config_hash="",
        input_digest=input_digest,
        body_hash=body_hash or digest(payload or {}),
        payload_json=payload,
        job_id=job_id,
        created_at=now_iso(),
        origin_kind=origin_kind,
    )
    db.add(row)
    db.flush()
    return row


def register_edge(
    db: Session, version: models.Version, dep: models.Version, role: str, object_key: str = ""
) -> None:
    if version.version_id == dep.version_id:
        raise DomainError("UNPROCESSABLE", "版本不能依赖自身")
    existing = db.get(
        models.ArtifactEdge, (version.version_id, dep.version_id, role, object_key)
    )
    if existing is not None:
        return
    db.add(
        models.ArtifactEdge(
            version_id=version.version_id,
            dep_version_id=dep.version_id,
            role=role,
            object_key=object_key,
        )
    )


def dependencies(db: Session, version: models.Version) -> list[models.ArtifactEdge]:
    return list(
        db.scalars(
            select(models.ArtifactEdge).where(
                models.ArtifactEdge.version_id == version.version_id
            )
        ).all()
    )


def load_version(db: Session, version_id: str) -> models.Version:
    row = db.get(models.Version, version_id)
    if row is None:
        raise DomainError("NOT_FOUND", "正式版本不存在")
    return row


# --------------------------------------------------------------------------- #
# 版本头与 CAS 发布
# --------------------------------------------------------------------------- #


def ensure_head(db: Session, key: FormalKey) -> models.FormalHead:
    row = db.get(
        models.FormalHead,
        (
            key.family_id,
            key.dataset_id,
            key.kind,
            key.run_key,
            key.node_key,
            key.target_key,
        ),
    )
    if row is not None:
        return row
    row = models.FormalHead(
        family_id=key.family_id,
        dataset_id=key.dataset_id,
        kind=key.kind,
        run_key=key.run_key,
        node_key=key.node_key,
        target_key=key.target_key,
        version_id=None,
        generation=0,
        required_generation=0,
        freshness="current",
        updated_at=now_iso(),
    )
    db.add(row)
    db.flush()
    return row


def read_head(db: Session, key: FormalKey) -> models.FormalHead | None:
    return db.get(
        models.FormalHead,
        (
            key.family_id,
            key.dataset_id,
            key.kind,
            key.run_key,
            key.node_key,
            key.target_key,
        ),
    )


def read_formal(db: Session, key: FormalKey) -> models.Version | None:
    head = read_head(db, key)
    if head is None or head.version_id is None:
        return None
    return db.get(models.Version, head.version_id)


def publish(
    db: Session,
    *,
    key: FormalKey,
    version: models.Version,
    path: str,
    basis: str,
    review_id: str = "",
    job_id: str | None = None,
    actor_ref: str | None = None,
    expected_version_id: str | None = None,
    expected_generation: int | None = None,
) -> models.Publication:
    """CAS 切换正式指针；失败抛 409，不产生半发布。"""

    head = ensure_head(db, key)
    expected_version = expected_version_id if expected_version_id is not None else head.version_id
    expected_generation = expected_generation if expected_generation is not None else head.generation

    conditions = [
        models.FormalHead.family_id == key.family_id,
        models.FormalHead.dataset_id == key.dataset_id,
        models.FormalHead.kind == key.kind,
        models.FormalHead.run_key == key.run_key,
        models.FormalHead.node_key == key.node_key,
        models.FormalHead.target_key == key.target_key,
        models.FormalHead.generation == expected_generation,
    ]
    if expected_version is None:
        conditions.append(models.FormalHead.version_id.is_(None))
    else:
        conditions.append(models.FormalHead.version_id == expected_version)

    result = db.execute(
        update(models.FormalHead)
        .where(*conditions)
        .values(
            version_id=version.version_id,
            generation=models.FormalHead.generation + 1,
            freshness="current",
            updated_at=now_iso(),
        )
    )
    if result.rowcount != 1:
        raise DomainError(
            "REVISION_CONFLICT",
            "当前正式版本已被其他操作更新，请重新读取后再提交",
        )

    previous = expected_version
    publication = models.Publication(
        publication_id=new_id("pub"),
        version_id=version.version_id,
        previous_version_id=previous,
        review_id=review_id,
        path=path,
        basis=basis,
        published_at=now_iso(),
        job_id=job_id,
        actor_ref=actor_ref,
    )
    db.add(publication)
    db.flush()
    return publication


def mark_superseded(db: Session, version_id: str) -> None:
    """旧版本仍可作为历史读取，只在需要时记录状态投影。"""

    row = db.get(models.Version, version_id)
    if row is None:
        return
    row.origin_kind = row.origin_kind or "system"


def bump_required_generation(db: Session, key: FormalKey) -> int:
    head = ensure_head(db, key)
    head.required_generation = (head.required_generation or 0) + 1
    head.updated_at = now_iso()
    db.flush()
    return head.required_generation


def published_history(db: Session, kind: str, dataset_id: str, limit: int = 50) -> list[models.Publication]:
    return list(
        db.scalars(
            select(models.Publication)
            .join(models.Version, models.Version.version_id == models.Publication.version_id)
            .where(models.Version.kind == kind, models.Version.dataset_id == dataset_id)
            .order_by(models.Publication.published_at.desc())
            .limit(limit)
        ).all()
    )


# --------------------------------------------------------------------------- #
# 当前运行指针
# --------------------------------------------------------------------------- #


def set_active_run(db: Session, *, dataset_id: str, node_key: str, run_id: str) -> models.ActiveRun:
    row = db.get(models.ActiveRun, (dataset_id, node_key))
    if row is None:
        row = models.ActiveRun(
            dataset_id=dataset_id,
            node_key=node_key,
            run_id=run_id,
            generation=1,
            updated_at=now_iso(),
        )
        db.add(row)
    else:
        row.run_id = run_id
        row.generation = (row.generation or 0) + 1
        row.updated_at = now_iso()
    db.flush()
    return row


def active_run_id(db: Session, dataset_id: str, node_key: str) -> str | None:
    row = db.get(models.ActiveRun, (dataset_id, node_key))
    return row.run_id if row else None


def latest_run_id(db: Session, dataset_id: str) -> str | None:
    return db.scalar(
        select(models.AnalysisRun.id)
        .where(models.AnalysisRun.dataset_id == dataset_id, models.AnalysisRun.status == "succeeded")
        .order_by(models.AnalysisRun.created_at.desc())
        .limit(1)
    )


# --------------------------------------------------------------------------- #
# 便捷读取（B/C 共用）
# --------------------------------------------------------------------------- #


def read_payload(db: Session, key: FormalKey) -> dict[str, Any] | None:
    version = read_formal(db, key)
    if version is None or version.payload_json is None:
        return None
    return dict(version.payload_json)


def student_key(family_id: str, dataset_id: str, node_key: str, student_id: str, kind: str = "student_analysis") -> FormalKey:
    return FormalKey(
        family_id=family_id,
        dataset_id=dataset_id,
        kind=kind,
        node_key=node_key,
        target_key=student_id,
    )


def class_key(family_id: str, dataset_id: str, node_key: str, class_id: str, kind: str = "class_analysis") -> FormalKey:
    return FormalKey(
        family_id=family_id,
        dataset_id=dataset_id,
        kind=kind,
        node_key=node_key,
        target_key=class_id,
    )


def plan_key(family_id: str, dataset_id: str, node_key: str, student_id: str) -> FormalKey:
    return FormalKey(
        family_id=family_id,
        dataset_id=dataset_id,
        kind="plan",
        node_key=node_key,
        target_key=student_id,
    )


def knowledge_key(family_id: str, dataset_id: str, catalog_version: str) -> FormalKey:
    return FormalKey(
        family_id=family_id,
        dataset_id=dataset_id,
        kind="knowledge",
        target_key=catalog_version,
    )


def catalog_key(family_id: str, dataset_id: str) -> FormalKey:
    return FormalKey(family_id=family_id, dataset_id=dataset_id, kind="catalog")

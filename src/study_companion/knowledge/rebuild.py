"""知识体系重建任务（02 §7、§9）。

教师对已有数据集重建知识体系时走这里：重新构建候选、模型补关系、独立复核，
通过则发布新版本，未通过的逐对象登记审核；不覆盖历史版本。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.jobs import register, upsert_step
from study_companion.jobs.registry import JobContext
from study_companion.knowledge.change_set import diff_payloads
from study_companion.knowledge.service import KnowledgeConfig, build_knowledge
from study_companion.library.datasets import load_catalog
from study_companion.model_service import ModelService


@register("knowledge_rebuild")
def handle_knowledge_rebuild(ctx: JobContext) -> dict[str, Any]:
    db = ctx.session
    scope = dict(ctx.job.scope_json or {})
    dataset_id = str(scope.get("dataset_id") or "")
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None or not dataset.family_id:
        from study_companion.contracts import DomainError

        raise DomainError("NOT_FOUND", "数据集不存在")

    upsert_step(db, ctx.job, "load_catalog", label="读取课程目录", status="running")
    db.commit()
    catalog_version = scope.get("catalog_version") or db.scalar(
        select(models.Catalog.version)
        .where(models.Catalog.dataset_id == dataset_id)
        .order_by(models.Catalog.created_at.desc())
        .limit(1)
    )
    if not catalog_version:
        from study_companion.contracts import DomainError

        raise DomainError("RESULT_NOT_READY", "该数据集还没有课程目录")
    catalog = load_catalog(db, catalog_version, dataset_id)
    upsert_step(db, ctx.job, "load_catalog", label="读取课程目录", status="succeeded")

    upsert_step(db, ctx.job, "build", label="构建知识点与关系", status="running")
    db.commit()
    from study_companion.pipeline import _textbook_candidates

    textbook_candidates, textbook_stats = _textbook_candidates(db, dataset_id, catalog)
    knowledge_version_id = repo.new_id("kn")
    result = build_knowledge(
        catalog,
        context=None,
        config=KnowledgeConfig(),
        dataset_id=dataset_id,
        knowledge_version=knowledge_version_id,
        model_configured=ModelService().configured,
        textbook_candidates=textbook_candidates,
        textbook_stats=textbook_stats,
    )
    snapshot = result.candidate_snapshot
    payload = snapshot.model_dump(mode="json")
    payload["review_info"] = result.review_info.model_dump(mode="json")
    payload["formal_object_ids"] = list(result.publishable_object_ids)

    key = repo.FormalKey(family_id=dataset.family_id, dataset_id=dataset_id, kind="knowledge")
    head = repo.read_head(db, key)
    previous_payload = {}
    if head is not None and head.version_id:
        previous = db.get(models.Version, head.version_id)
        if previous is not None and previous.payload_json:
            previous_payload = dict(previous.payload_json)
    changes = diff_payloads(previous_payload, payload) if previous_payload else []

    version = repo.create_version(
        db,
        kind="knowledge",
        key=key,
        payload=payload,
        rule_version=KnowledgeConfig().rule_version,
        input_digest=repo.digest(payload),
        job_id=ctx.job.id,
        version_id=knowledge_version_id,
        origin_kind="system",
    )
    catalog_row = db.get(models.Catalog, catalog_version)
    if catalog_row is not None:
        catalog_key = repo.FormalKey(
            family_id=dataset.family_id, dataset_id=dataset_id, kind="catalog"
        )
        catalog_head = repo.read_head(db, catalog_key)
        if catalog_head is not None and catalog_head.version_id:
            catalog_version_row = db.get(models.Version, catalog_head.version_id)
            if catalog_version_row is not None:
                repo.register_edge(db, version, catalog_version_row, role="catalog")
    published = False
    if result.review_info.status == "validated":
        repo.publish(
            db,
            key=key,
            version=version,
            path="system_auto",
            basis="automatic_review",
            job_id=ctx.job.id,
            expected_version_id=head.version_id if head else None,
            expected_generation=head.generation if head else 0,
        )
        published = True
    else:
        from study_companion.knowledge.review_service import ensure_review_item

        review_objects = (
            ("point", snapshot.points, "kp_id"),
            ("relation", snapshot.relations, "relation_id"),
            ("annotation", snapshot.annotations, "annotation_id"),
        )
        for object_type, items, id_field in review_objects:
            for item in items:
                if item.review_info is not None and item.review_info.status == "validated":
                    continue
                ensure_review_item(
                    db,
                    dataset_id=dataset_id,
                    knowledge_version=knowledge_version_id,
                    object_type=object_type,
                    object_id=getattr(item, id_field),
                    reason="重建候选未通过全部校验，需教师确认",
                )
    upsert_step(db, ctx.job, "build", label="构建知识点与关系", status="succeeded")
    db.commit()

    return {
        "dataset_id": dataset_id,
        "knowledge_version": knowledge_version_id,
        "published": published,
        "points": len(snapshot.points),
        "relations": len(snapshot.relations),
        "annotations": len(snapshot.annotations),
        "changes": {
            "added": sum(1 for item in changes if item.kind == "added"),
            "removed": sum(1 for item in changes if item.kind == "removed"),
            "renamed": sum(1 for item in changes if item.kind == "renamed"),
            "changed": sum(1 for item in changes if item.kind == "changed"),
        },
        "limitations": snapshot.limitations,
    }

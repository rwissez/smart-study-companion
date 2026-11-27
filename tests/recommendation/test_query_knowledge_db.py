"""S1-01：材料检索的知识点映射以数据库正式知识为主要来源。

覆盖：

* 只有数据库正式知识时，``kp_ids`` 筛选可用，未进入正式对象白名单的候选不生效；
* 旧文件 ``KnowledgeStore`` 快照仍可作为回退，但学生不能把未发布旧稿当正式依据；
* 跨数据集/跨目录的正式知识被拒绝，未发布知识返回明确的空结果与限制；
* 调用方显式传入正式正文时可直接用于筛选。
"""

from __future__ import annotations

import pytest

from study_companion.config import get_settings
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.store import KnowledgeStore
from study_companion.recommendation.query import search_formal_materials

DATASET_ID = "ds-query-kb"
FAMILY_ID = "fam-query-kb"
CATALOG_VERSION = "cat-query-kb"
KNOWLEDGE_VERSION = "kn-query-kb"


def _seed_dataset(
    session,
    *,
    dataset_id: str = DATASET_ID,
    family_id: str = FAMILY_ID,
    catalog_version: str = CATALOG_VERSION,
) -> None:
    now = repo.now_iso()
    session.add(
        models.DatasetFamily(
            family_id=family_id,
            label="知识点筛选测试",
            data_origin="synthetic",
            created_at=now,
            generation=1,
        )
    )
    session.flush()
    session.add(
        models.Dataset(
            id=dataset_id,
            created_at=now,
            parent_id=None,
            status="ready",
            family_id=family_id,
            manifest_json={"synthetic": True},
            state="ready",
        )
    )
    session.flush()
    session.add(
        models.Catalog(
            version=catalog_version,
            dataset_id=dataset_id,
            content_hash="0" * 64,
            created_at=now,
            payload_json={"topics": [], "links": []},
        )
    )
    for indicator_id in ("I1", "I2"):
        session.add(
            models.Indicator(
                catalog_version=catalog_version,
                indicator_id=indicator_id,
                payload_json={
                    "indicator_id": indicator_id,
                    "topic_id": "T1",
                    "description": f"观察指标 {indicator_id}",
                },
            )
        )
    for material_id, indicator_id in (("MAT-I1", "I1"), ("MAT-I2", "I2")):
        session.add(
            models.Material(
                catalog_version=catalog_version,
                material_id=material_id,
                payload_json={
                    "material_id": material_id,
                    "material_type": "question",
                    "topic_ids": ["T1"],
                    "indicator_ids": [indicator_id],
                    "content": f"材料 {material_id}",
                    "source_locator": {
                        "source_file": "synthetic.csv",
                        "verification_status": "verified",
                    },
                    "question_type": "solution",
                    "reference_answer": "参考解答",
                    "max_score": 5,
                },
            )
        )
    session.commit()


def _knowledge_payload(
    *,
    dataset_id: str = DATASET_ID,
    catalog_version: str = CATALOG_VERSION,
    knowledge_version: str | None = KNOWLEDGE_VERSION,
    formal_object_ids: list[str] | None = None,
) -> dict:
    payload: dict = {
        "dataset_id": dataset_id,
        "catalog_version": catalog_version,
        "points": [
            {"kp_id": "K1", "name": "知识点一", "indicator_ids": ["I1"]},
            {"kp_id": "K2", "name": "知识点二", "indicator_ids": ["I2"]},
        ],
        "formal_object_ids": formal_object_ids or ["K1", "K2"],
    }
    if knowledge_version is not None:
        payload["knowledge_version"] = knowledge_version
    return payload


def _publish_knowledge(session, payload: dict) -> models.Version:
    key = repo.FormalKey(
        family_id=FAMILY_ID,
        dataset_id=payload.get("dataset_id", DATASET_ID),
        kind="knowledge",
    )
    version = repo.create_version(
        session,
        kind="knowledge",
        key=key,
        payload=payload,
        version_id=payload["knowledge_version"],
    )
    repo.publish(
        session,
        key=key,
        version=version,
        path="system_auto",
        basis="automatic_review",
    )
    session.commit()
    return version


def _call(session, **overrides):
    params = {
        "dataset_id": DATASET_ID,
        "catalog_version": CATALOG_VERSION,
        "role": "teacher",
        "purpose": "practice",
        "kp_ids": ["K1"],
    }
    params.update(overrides)
    return search_formal_materials(session, **params)


def test_db_formal_knowledge_drives_kp_filter(session) -> None:
    _seed_dataset(session)
    _publish_knowledge(session, _knowledge_payload())

    result = _call(session)

    assert [item["material_id"] for item in result["items"]] == ["MAT-I1"]
    assert result["total"] == 1
    assert "正式知识版本" in " ".join(result["limitations"])


def test_db_formal_knowledge_wins_over_file_snapshot(session) -> None:
    _seed_dataset(session)
    _publish_knowledge(session, _knowledge_payload())
    KnowledgeStore(get_settings().data_root).save_draft(
        DATASET_ID,
        CATALOG_VERSION,
        KnowledgeBase(
            kb_version="kb-stale",
            status="draft",
            points=[
                KnowledgePoint(
                    kp_id="K1",
                    name="旧快照知识点",
                    indicator_ids=["I2"],
                    verification_status="verified",
                    definition_locator=Locator(
                        source_file="old.pdf",
                        file_page=2,
                        verification_status="verified",
                    ),
                )
            ],
        ),
    )

    result = _call(session)

    assert [item["material_id"] for item in result["items"]] == ["MAT-I1"]
    assert "数据库正式知识版本" in " ".join(result["limitations"])


def test_formal_object_whitelist_keeps_unpublished_points_out(session) -> None:
    _seed_dataset(session)
    _publish_knowledge(session, _knowledge_payload(formal_object_ids=["K1"]))

    with pytest.raises(DomainError) as exc:
        _call(session, kp_ids=["K2"])

    assert exc.value.code == "NOT_FOUND"


def test_student_view_still_strips_reference_answers(session) -> None:
    _seed_dataset(session)
    _publish_knowledge(session, _knowledge_payload())

    result = _call(session, role="student")

    assert result["items"][0]["material_id"] == "MAT-I1"
    assert "reference_answer" not in result["items"][0]
    assert "reference_explanation" not in result["items"][0]


def test_file_snapshot_is_still_a_teacher_fallback(session) -> None:
    _seed_dataset(session)
    KnowledgeStore(get_settings().data_root).save_draft(
        DATASET_ID,
        CATALOG_VERSION,
        KnowledgeBase(
            kb_version="kb-query-file",
            status="draft",
            points=[
                KnowledgePoint(
                    kp_id="K-file",
                    name="旧快照知识点",
                    indicator_ids=["I1"],
                    verification_status="verified",
                    definition_locator=Locator(
                        source_file="old.pdf",
                        file_page=2,
                        verification_status="verified",
                    ),
                )
            ],
        ),
    )

    result = _call(session, kp_ids=["K-file"])

    assert [item["material_id"] for item in result["items"]] == ["MAT-I1"]
    assert "旧文件知识快照" in " ".join(result["limitations"])


def test_student_cannot_filter_with_unpublished_file_snapshot(session) -> None:
    _seed_dataset(session)
    KnowledgeStore(get_settings().data_root).save_draft(
        DATASET_ID,
        CATALOG_VERSION,
        KnowledgeBase(
            kb_version="kb-query-draft",
            status="draft",
            points=[
                KnowledgePoint(
                    kp_id="K-file",
                    name="旧快照知识点",
                    indicator_ids=["I1"],
                    verification_status="verified",
                    definition_locator=Locator(
                        source_file="old.pdf",
                        file_page=2,
                        verification_status="verified",
                    ),
                )
            ],
        ),
    )

    result = _call(session, role="student", kp_ids=["K-file"])

    assert result["items"] == []
    assert result["total"] == 0
    assert "未标记为正式发布" in " ".join(result["limitations"])


def test_formal_knowledge_for_another_catalog_is_rejected(session) -> None:
    _seed_dataset(session)
    _publish_knowledge(
        session,
        _knowledge_payload(catalog_version="cat-other", knowledge_version="kn-other-cat"),
    )

    with pytest.raises(DomainError) as exc:
        _call(session)

    assert exc.value.code == "CONTEXT_MISMATCH"


def test_knowledge_from_another_dataset_is_not_used(session) -> None:
    _seed_dataset(session)
    _seed_dataset(
        session,
        dataset_id="ds-other",
        family_id="fam-other",
        catalog_version="cat-other",
    )
    _publish_knowledge(
        session,
        _knowledge_payload(
            dataset_id="ds-other",
            catalog_version="cat-other",
            knowledge_version="kn-other-dataset",
        ),
    )

    result = _call(session)

    assert result["items"] == []
    assert result["total"] == 0
    assert "没有已发布的正式知识版本" in " ".join(result["limitations"])


def test_unpublished_knowledge_returns_explicit_empty_result(session) -> None:
    _seed_dataset(session)
    key = repo.FormalKey(family_id=FAMILY_ID, dataset_id=DATASET_ID, kind="knowledge")
    repo.create_version(
        session,
        kind="knowledge",
        key=key,
        payload=_knowledge_payload(),
        version_id=KNOWLEDGE_VERSION,
    )
    session.commit()

    result = _call(session)

    assert result["items"] == []
    assert result["total"] == 0
    assert "尚未发布为正式版本" in " ".join(result["limitations"])


def test_no_knowledge_at_all_returns_limitation_not_exception(session) -> None:
    _seed_dataset(session)

    result = _call(session)

    assert result["items"] == []
    assert result["total"] == 0
    assert "没有已发布的正式知识版本" in " ".join(result["limitations"])


def test_caller_supplied_payload_can_be_used_without_db_version(session) -> None:
    _seed_dataset(session)
    payload = _knowledge_payload(knowledge_version=None)

    result = _call(session, knowledge_payload=payload)

    assert [item["material_id"] for item in result["items"]] == ["MAT-I1"]
    assert "调用方传入" in " ".join(result["limitations"])


def test_caller_payload_with_another_dataset_is_rejected(session) -> None:
    _seed_dataset(session)

    with pytest.raises(DomainError) as exc:
        _call(
            session,
            knowledge_payload=_knowledge_payload(
                dataset_id="ds-other", knowledge_version=None
            ),
        )

    assert exc.value.code == "CONTEXT_MISMATCH"

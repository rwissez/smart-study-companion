"""S1-01：Agent 知识查询以数据库正式版本为主来源。

正式导入只写 ``versions`` / ``formal_heads``，因此干净部署下不能再依赖本地
``KnowledgeStore`` 快照。这里覆盖：

* 只有 DB 正式知识时 locate / detail / relations 可用；
* 学生可见性仍按对象与来源核验状态裁剪，且查询不写库；
* 未发布、跨数据集、跨目录的正式知识被拒绝；
* 旧文件快照仍可作为回退。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from study_companion.agent.expanded_tools import DB_SESSION_KEY
from study_companion.agent.knowledge_tools import build_knowledge_registry
from study_companion.agent.tasks import get_task
from study_companion.agent.types import ToolCall
from study_companion.contracts import Catalog, DomainError, Indicator, Topic
from study_companion.db import models, version_repository as repo
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.store import KnowledgeStore
from study_companion.tools.b_analysis import ServerContext


def _context(
    dataset_id: str = "dataset-a",
    catalog_version: str = "cat-a",
    session=None,
) -> ServerContext:
    extras = {DB_SESSION_KEY: session} if session is not None else {}
    return ServerContext(
        "", dataset_id, catalog_version, frozenset(), None, None, extras=extras
    )


def _catalog(version: str = "cat-a") -> Catalog:
    return Catalog(
        version=version,
        topics=[Topic(topic_id="T1", title="Algebra")],
        indicators=[Indicator(indicator_id="I1", topic_id="T1", description="Operations")],
        materials=[],
        links=[],
    )


def _seed_scope(session, dataset_id: str = "dataset-a", family_id: str = "fam-a") -> None:
    session.add(
        models.DatasetFamily(
            family_id=family_id,
            label="知识工具测试",
            data_origin="official",
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    session.add(
        models.Dataset(
            id=dataset_id,
            created_at=repo.now_iso(),
            parent_id=None,
            status="ready",
            manifest_json={},
            family_id=family_id,
            state="ready",
        )
    )
    session.flush()


def _point(
    kp_id: str,
    name: str,
    *,
    aliases: list[str] | None = None,
    locators: list[dict] | None = None,
    topic_ids: list[str] | None = None,
    indicator_ids: list[str] | None = None,
) -> dict:
    return {
        "kp_id": kp_id,
        "name": name,
        "aliases": aliases or [],
        "kind": "concept",
        "snippet": f"{name} 的短定义",
        "source_locators": locators or [],
        "topic_ids": topic_ids or ["T1"],
        "indicator_ids": indicator_ids or ["I1"],
    }


def _payload(
    *,
    dataset_id: str = "dataset-a",
    catalog_version: str = "cat-a",
    knowledge_version: str = "kn-db",
    points: list[dict] | None = None,
    relations: list[dict] | None = None,
    annotations: list[dict] | None = None,
) -> dict:
    return {
        "dataset_id": dataset_id,
        "catalog_version": catalog_version,
        "knowledge_version": knowledge_version,
        "points": points if points is not None else _default_points(),
        "relations": relations
        if relations is not None
        else [
            {
                "relation_id": "R1",
                "type": "prerequisite",
                "source_ids": ["K1"],
                "target_id": "K2",
                "basis": "textbook",
                "note": "先学多项式再做因式分解",
            }
        ],
        "annotations": annotations
        if annotations is not None
        else [
            {
                "annotation_id": "A1",
                "source_type": "material",
                "source_id": "M1",
                "target_type": "knowledge_point",
                "target_ids": ["K1"],
                "knowledge_role": "primary",
                "evidence": "材料中先讲多项式",
            }
        ],
    }


def _default_points() -> list[dict]:
    return [
        _point(
            "K1",
            "Polynomial",
            aliases=["Poly"],
            locators=[
                {
                    "source_file": "book.pdf",
                    "pdf_page": 4,
                    "printed_page": "12",
                    "verification_status": "verified",
                }
            ],
        ),
        _point(
            "K2",
            "Factor theorem",
            locators=[
                {
                    "source_file": "book.pdf",
                    "pdf_page": 9,
                    "verification_status": "unverified",
                }
            ],
        ),
        _point("K3", "No source point"),
    ]


def _publish(session, payload: dict, *, family_id: str = "fam-a", dataset_id: str = "dataset-a"):
    key = repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="knowledge")
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
    return version


def _call(registry, name: str, arguments: dict) -> dict:
    return registry.call(ToolCall(call_id=f"call-{name}", name=name, arguments=arguments))


def test_db_formal_knowledge_serves_locate_detail_and_relations(
    tmp_path: Path, session
) -> None:
    _seed_scope(session)
    version = _publish(session, _payload())
    # 同一数据目录再放一份旧文件快照：DB 正式知识必须优先。
    KnowledgeStore(tmp_path / "data").save_draft(
        "dataset-a",
        "cat-a",
        KnowledgeBase(
            kb_version="kb-file",
            points=[
                KnowledgePoint(
                    kp_id="K-file",
                    name="Polynomial",
                    verification_status="verified",
                    definition_locator=Locator(
                        source_file="old.pdf",
                        file_page=1,
                        verification_status="verified",
                    ),
                )
            ],
        ),
    )
    registry = build_knowledge_registry(
        _context(session=session),
        "teacher",
        get_task("knowledge_lookup"),
        tmp_path / "data",
        _catalog(),
    )

    located = _call(registry, "locate_knowledge", {"keyword": "Poly"})
    assert [item["kp_id"] for item in located["data"]] == ["K1"]
    assert located["data"][0]["kb_version"] == version.version_id
    assert located["data"][0]["locator_verification_status"] == "verified"
    assert located["evidence_refs"] == [
        {"kind": "curriculum", "id": "K1", "catalog_version": "cat-a"}
    ]

    detail = _call(registry, "get_knowledge_detail", {"kp_id": "K1"})
    data = detail["data"]
    assert data["kb_version"] == version.version_id
    assert data["locator"]["source_file"] == "book.pdf"
    assert data["locator"]["file_page"] == 4
    assert data["locator"]["verification_status"] == "verified"
    assert [item["annotation_id"] for item in data["annotations"]] == ["A1"]

    # 没有来源定位的知识点必须落成明确的 unknown locator，且不能声称 verified。
    unknown = _call(registry, "get_knowledge_detail", {"kp_id": "K3"})
    assert unknown["data"]["locator"]["source_file"] == "unknown"
    assert unknown["data"]["locator"]["verification_status"] == "unverified"

    relations_registry = build_knowledge_registry(
        _context(session=session),
        "teacher",
        get_task("prerequisite_query"),
        tmp_path / "data",
        _catalog(),
    )
    relations = _call(
        relations_registry,
        "get_knowledge_relations",
        {"kp_id": "K2", "direction": "upstream"},
    )
    assert [item["relation_id"] for item in relations["data"]] == ["R1"]
    assert relations["data"][0]["type"] == "prerequisite"
    assert relations["data"][0]["kb_version"] == version.version_id


def test_student_db_visibility_is_filtered_and_read_only(
    tmp_path: Path, session
) -> None:
    _seed_scope(session)
    _publish(session, _payload())
    before_versions = session.scalar(select(func.count()).select_from(models.Version))
    before_heads = session.scalar(select(func.count()).select_from(models.FormalHead))
    before_publications = session.scalar(
        select(func.count()).select_from(models.Publication)
    )
    registry = build_knowledge_registry(
        _context(session=session),
        "student",
        get_task("knowledge_lookup"),
        tmp_path / "data",
        _catalog(),
    )

    # K1 的来源已核验，学生可见；K2 的来源未核验，学生不可见。
    assert [
        item["kp_id"]
        for item in _call(registry, "locate_knowledge", {"keyword": "Polynomial"})["data"]
    ] == ["K1"]
    assert _call(registry, "locate_knowledge", {"keyword": "Factor"})["data"] == []
    with pytest.raises(DomainError) as exc:
        _call(registry, "get_knowledge_detail", {"kp_id": "K2"})
    assert exc.value.code == "NOT_FOUND"

    # 学生不能借关系审阅任务拿到教师候选列表。
    with pytest.raises(DomainError) as forbidden:
        build_knowledge_registry(
            _context(session=session),
            "student",
            get_task("relation_review"),
            tmp_path / "data",
            _catalog(),
        )
    assert forbidden.value.code == "FORBIDDEN"

    # 只读：查询前后正式版本、正式头和发布记录都不变。
    assert session.scalar(select(func.count()).select_from(models.Version)) == before_versions
    assert session.scalar(select(func.count()).select_from(models.FormalHead)) == before_heads
    assert (
        session.scalar(select(func.count()).select_from(models.Publication))
        == before_publications
    )


@pytest.mark.parametrize("role", ["teacher", "student"])
def test_unpublished_db_knowledge_is_rejected(
    tmp_path: Path, session, role: str
) -> None:
    _seed_scope(session)
    key = repo.FormalKey(family_id="fam-a", dataset_id="dataset-a", kind="knowledge")
    repo.create_version(
        session,
        kind="knowledge",
        key=key,
        payload=_payload(),
        version_id="kn-draft",
    )
    with pytest.raises(DomainError) as exc:
        build_knowledge_registry(
            _context(session=session),
            role,
            get_task("knowledge_lookup"),
            tmp_path / "data",
            _catalog(),
        )
    assert exc.value.code == "KB_NOT_PUBLISHED"


def test_cross_dataset_formal_knowledge_is_rejected(
    tmp_path: Path, session
) -> None:
    _seed_scope(session)
    _seed_scope(session, dataset_id="dataset-b", family_id="fam-b")
    _publish(session, _payload(dataset_id="dataset-b", knowledge_version="kn-other"))
    with pytest.raises(DomainError) as exc:
        build_knowledge_registry(
            _context(session=session),
            "teacher",
            get_task("knowledge_lookup"),
            tmp_path / "data",
            _catalog(),
        )
    assert exc.value.code == "CONTEXT_MISMATCH"


def test_formal_knowledge_for_another_catalog_is_rejected(
    tmp_path: Path, session
) -> None:
    _seed_scope(session)
    _publish(session, _payload(catalog_version="cat-b", knowledge_version="kn-cat-b"))
    with pytest.raises(DomainError) as exc:
        build_knowledge_registry(
            _context(session=session),
            "teacher",
            get_task("knowledge_lookup"),
            tmp_path / "data",
            _catalog(),
        )
    assert exc.value.code == "CONTEXT_MISMATCH"


def test_file_snapshot_is_still_a_fallback(tmp_path: Path, session) -> None:
    _seed_scope(session)
    data_root = tmp_path / "data"
    KnowledgeStore(data_root).save_draft(
        "dataset-a",
        "cat-a",
        KnowledgeBase(
            kb_version="kb-file",
            points=[
                KnowledgePoint(
                    kp_id="K-file",
                    name="Polynomial",
                    aliases=["Poly"],
                    snippet="旧快照定义",
                    topic_ids=["T1"],
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
    registry = build_knowledge_registry(
        _context(session=session),
        "teacher",
        get_task("knowledge_lookup"),
        data_root,
        _catalog(),
    )
    located = _call(registry, "locate_knowledge", {"keyword": "Poly"})
    assert [item["kp_id"] for item in located["data"]] == ["K-file"]
    assert located["data"][0]["kb_version"] == "kb-file"

from __future__ import annotations

import pytest

from study_companion.contracts import Catalog, DomainError, Topic, Indicator
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.query import get_knowledge_detail, get_knowledge_relations, list_knowledge_candidates, locate_knowledge


def _data():
    catalog = Catalog(version="cat-1", topics=[Topic(topic_id="T1", title="数与式")], indicators=[Indicator(indicator_id="I1", topic_id="T1", description="因式分解")], materials=[], links=[])
    points = [
        KnowledgePoint(kp_id="K1", name="因式分解", definition_locator=Locator(source_file="book.pdf", file_page=2, verification_status="verified"), verification_status="verified", topic_ids=["T1"], indicator_ids=["I1"]),
        KnowledgePoint(kp_id="K2", name="待核对方法", definition_locator=Locator(source_file="book.pdf", file_page=3), verification_status="unverified"),
    ]
    kb = KnowledgeBase(kb_version="kb-1", status="verified", points=points,
        relations=[Relation(relation_id="R1", type="derives_from", source_ids=["K2"], target_id="K1", verification_status="verified"), Relation(relation_id="R2", type="special_case", source_ids=["K1"], target_id="K2", verification_status="candidate")],
        annotations=[Annotation(candidate_id="A1", source_type="material", source_id="M1", target_type="knowledge_point", target_ids=["K2"], evidence="短摘")])
    return kb, catalog


def test_student_queries_only_return_verified_knowledge_and_relations():
    kb, catalog = _data()
    assert [item["kp_id"] for item in locate_knowledge(kb, catalog, "因式分解", role="student", kb_version="kb-1", catalog_version="cat-1")] == ["K1"]
    assert locate_knowledge(kb, catalog, "待核对方法", role="student", kb_version="kb-1", catalog_version="cat-1") == []
    assert get_knowledge_relations(kb, catalog, "K1", role="student", kb_version="kb-1", catalog_version="cat-1") == []


def test_teacher_candidate_view_and_version_binding():
    kb, catalog = _data()
    candidates = list_knowledge_candidates(kb, catalog, role="teacher", kb_version="kb-1", catalog_version="cat-1")
    assert candidates["total"] == 2
    assert "page text" not in str(get_knowledge_detail(kb, catalog, "K1", role="teacher", kb_version="kb-1", catalog_version="cat-1"))
    with pytest.raises(DomainError, match="目录版本"):
        locate_knowledge(kb, catalog, "因式分解", role="teacher", kb_version="kb-1", catalog_version="other")
    with pytest.raises(DomainError) as exc:
        list_knowledge_candidates(kb, catalog, role="student", kb_version="kb-1", catalog_version="cat-1")
    assert exc.value.code == "FORBIDDEN"


def test_rejected_points_hidden_from_normal_teacher_queries():
    kb, catalog = _data()
    kb.points[0].verification_status = "rejected"
    assert locate_knowledge(kb, catalog, "因式分解", role="teacher", kb_version="kb-1", catalog_version="cat-1") == []
    with pytest.raises(DomainError):
        get_knowledge_detail(kb, catalog, "K1", role="teacher", kb_version="kb-1", catalog_version="cat-1")


def test_unknown_roles_and_invalid_filters_fail_closed():
    kb, catalog = _data()
    with pytest.raises(DomainError):
        locate_knowledge(kb, catalog, "因式分解", role="admin", kb_version="kb-1", catalog_version="cat-1")
    with pytest.raises(DomainError):
        list_knowledge_candidates(kb, catalog, role="teacher", kb_version="kb-1", catalog_version="cat-1", status="anything")
    with pytest.raises(DomainError):
        get_knowledge_relations(kb, catalog, "K1", role="teacher", kb_version="kb-1", catalog_version="cat-1", depth=True)


def test_student_detail_filters_unverified_source_locators():
    kb, catalog = _data()
    kb.points[0].source_locators = [Locator(source_file="private.pdf", file_page=1)]
    detail = get_knowledge_detail(kb, catalog, "K1", role="student", kb_version="kb-1", catalog_version="cat-1")
    assert detail["source_locators"] == []


def test_traversal_direction_depth_and_type_filter():
    kb, catalog = _data()
    kb.points[1].verification_status = "verified"
    kb.points[1].definition_locator.verification_status = "verified"
    kb.points.append(kb.points[0].model_copy(update={"kp_id": "K3", "name": "third"}))
    kb.relations.append(Relation(relation_id="R3", type="derives_from", source_ids=["K3"], target_id="K2", verification_status="verified"))
    args = dict(role="student", kb_version="kb-1", catalog_version="cat-1", direction="upstream")
    assert [r["relation_id"] for r in get_knowledge_relations(kb, catalog, "K1", depth=1, **args)] == ["R1"]
    assert [r["relation_id"] for r in get_knowledge_relations(kb, catalog, "K1", depth=2, **args)] == ["R1", "R3"]
    assert get_knowledge_relations(kb, catalog, "K1", types={"composed_of"}, **args) == []


def test_hidden_exact_match_does_not_shadow_visible_alias():
    kb, catalog = _data()
    kb.points[0].aliases = [kb.points[1].name]
    hits = locate_knowledge(kb, catalog, kb.points[1].name, role="student", kb_version="kb-1", catalog_version="cat-1")
    assert [hit["kp_id"] for hit in hits] == ["K1"]


def test_candidate_filters_and_pagination():
    kb, catalog = _data()
    args = dict(role="teacher", kb_version="kb-1", catalog_version="cat-1")
    assert list_knowledge_candidates(kb, catalog, category="knowledge_point", **args)["total"] == 1
    assert list_knowledge_candidates(kb, catalog, status="verified", **args)["total"] == 1
    assert list_knowledge_candidates(kb, catalog, page=3, page_size=1, **args)["items"] == []

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from study_companion.contracts import Catalog, DomainError, Material, SourceLocator


def _catalog():
    return Catalog(version="cat", topics=[], indicators=[], links=[], materials=[Material(
        material_id="M1", material_type="card", topic_ids=[], indicator_ids=[], content="概念",
        source_locator=SourceLocator(source_file="book", pdf_page=1, verification_status="verified"),
    )])
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.review import ReviewDecision, review_candidates


def _kb() -> KnowledgeBase:
    point = KnowledgePoint(
        kp_id="K1", name="概念", definition_locator=Locator(source_file="book", file_page=1)
    )
    relation = Relation(
        relation_id="R1", type="derives_from", source_ids=["K1"], target_id="K2",
        evidence="原文", evidence_file_page=1, source_file="book", proposer="rule",
    )
    return KnowledgeBase(
        kb_version="draft-1", points=[point, point.model_copy(update={"kp_id": "K2", "name": "结果"})],
        relations=[relation],
        annotations=[Annotation(
            candidate_id="A1", source_type="material", source_id="cat/M1",
            target_type="knowledge_point", target_ids=["K2"], proposer="rule",
            evidence="概念", source_file="book", file_page=1,
        )],
    )


def test_review_returns_new_immutable_verified_snapshot() -> None:
    base = _kb()
    reviewed = review_candidates(
        "draft-1",
        [ReviewDecision("A1", "accept"), ReviewDecision("R1", "accept")],
        "teacher-1",
        base,
        new_version="draft-2", catalog=_catalog(),
        page_text={("book", 1): "原文 概念"},
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert reviewed.kb_version == "draft-2"
    assert reviewed.parent_version == "draft-1"
    assert reviewed.annotations[0].verification_status == "verified"
    assert reviewed.relations[0].verification_status == "verified"
    assert reviewed.relations[0].reviewed_by == "teacher-1"
    assert base.kb_version == "draft-1"
    assert base.annotations[0].verification_status == "unverified"
    assert base.relations[0].verification_status == "candidate"


def test_reject_keeps_candidate_hidden_and_edit_target_revalidates() -> None:
    base = _kb()
    reviewed = review_candidates(
        "draft-1",
        [ReviewDecision("A1", "edit-target", target_ids=("K2",)), ReviewDecision("R1", "reject")],
        "teacher-1",
        base,
        new_version="draft-2", catalog=_catalog(),
        page_text={("book", 1): "概念"},
    )
    assert reviewed.annotations[0].verification_status == "unverified"
    assert reviewed.annotations[0].target_ids == ["K2"]
    assert reviewed.relations[0].verification_status == "rejected"


def test_invalid_relation_evidence_fails_without_returning_partial_result() -> None:
    with pytest.raises(DomainError) as exc:
        review_candidates(
            "draft-1", [ReviewDecision("R1", "accept")], "teacher-1", _kb(),
            new_version="draft-2", catalog=_catalog(), page_text={("book", 1): "different"},
        )
    assert exc.value.code == "RELATION_REVIEW_INVALID"


def test_review_requires_unique_new_version_and_existing_candidates() -> None:
    with pytest.raises(DomainError) as exc:
        review_candidates("draft-1", [ReviewDecision("missing", "accept")], "teacher", _kb(), new_version="draft-2", catalog=_catalog())
    assert exc.value.code == "CANDIDATE_NOT_FOUND"

    with pytest.raises(DomainError) as exc:
        review_candidates("draft-1", [], "teacher", _kb(), new_version="draft-1", catalog=_catalog())
    assert exc.value.code == "INVALID_KB_VERSION"


def test_point_accept_requires_real_evidence_and_marks_locator():
    base = _kb()
    result = review_candidates("draft-1", [ReviewDecision("K1", "accept")], "teacher", base,
                               new_version="draft-2", catalog=_catalog(), page_text={("book", 1): "source"})
    assert result.points[0].verification_status == "verified"
    assert result.points[0].definition_locator.verification_status == "verified"
    assert base.points[0].verification_status == "unverified"
    with pytest.raises(DomainError):
        review_candidates("draft-1", [ReviewDecision("K1", "accept")], "teacher", base,
                          new_version="draft-3", catalog=_catalog())


def test_review_requires_catalog_and_rejects_unknown_actions():
    with pytest.raises(DomainError) as exc:
        review_candidates("draft-1", [], "teacher", _kb(), new_version="draft-2")
    assert exc.value.code == "CATALOG_REQUIRED"
    with pytest.raises(DomainError) as exc:
        review_candidates("draft-1", [ReviewDecision("K1", "publish")], "teacher", _kb(),
                          new_version="draft-2", catalog=_catalog())
    assert exc.value.code == "INVALID_DECISION"

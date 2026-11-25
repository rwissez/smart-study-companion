from __future__ import annotations

import pytest

from study_companion.contracts import Catalog, DomainError
from study_companion.knowledge.dedup import deduplicate_knowledge
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.query import list_knowledge_candidates
from study_companion.knowledge.review import ReviewDecision, review_candidates


def _point(kp_id, name="Vector", snippet="A directed quantity", **kwargs):
    return KnowledgePoint(kp_id=kp_id, name=name, snippet=snippet,
                          definition_locator=Locator(source_file=f"{kp_id}.pdf", file_page=1), **kwargs)


def _catalog():
    return Catalog(version="cat", topics=[], indicators=[], materials=[], links=[])


def test_cross_source_exact_normalization_preserves_provenance_and_references():
    kb = KnowledgeBase(kb_version="v1", points=[_point("A"), _point("B", name="Ｖｅｃｔｏｒ", snippet="A directed\nquantity")],
                       annotations=[Annotation(material_id="M", kp_ids=["B"])])
    result = deduplicate_knowledge(kb)
    assert [p.kp_id for p in result.points] == ["A"]
    assert {loc.source_file for loc in result.points[0].source_locators} == {"A.pdf", "B.pdf"}
    assert result.annotations[0].kp_ids == ["A"]
    assert len(kb.points) == 2
    assert deduplicate_knowledge(result) == result


def test_alias_match_requires_exact_nonempty_definition_and_compatible_kind():
    kb = KnowledgeBase(kb_version="v1", points=[_point("A", aliases=["Arrow"]), _point("B", name="arrow")])
    assert len(deduplicate_knowledge(kb).points) == 1
    kb.points[1].kind = "method"
    assert len(deduplicate_knowledge(kb).points) == 2
    kb.points[0].snippet = kb.points[1].snippet = ""
    assert len(deduplicate_knowledge(kb).points) == 2


def test_homonyms_are_teacher_readable_proposals_never_auto_merged():
    kb = KnowledgeBase(kb_version="v1", points=[_point("A"), _point("B", snippet="Different meaning")])
    result = deduplicate_knowledge(kb)
    assert len(result.points) == 2
    candidate = result.merge_candidates[0]
    assert candidate.source_locator.source_file == "B.pdf"
    assert candidate.target_snippet == "A directed quantity"
    assert "同名异义" in candidate.reason
    kwargs = dict(kb_version="v1", catalog_version="cat", category="merge")
    view = list_knowledge_candidates(result, _catalog(), role="teacher", **kwargs)
    assert view["total"] == 1
    with pytest.raises(DomainError) as exc:
        list_knowledge_candidates(result, _catalog(), role="student", **kwargs)
    assert exc.value.code == "FORBIDDEN"


def test_review_reject_is_stable_and_accept_requires_both_source_evidence():
    base = deduplicate_knowledge(KnowledgeBase(kb_version="v1", points=[_point("A"), _point("B", snippet="Different meaning")]))
    candidate = base.merge_candidates[0]
    kwargs = dict(new_version="v2", catalog=_catalog())
    rejected = review_candidates("v1", [ReviewDecision(candidate.candidate_id, "reject")], "teacher", base, **kwargs)
    assert deduplicate_knowledge(rejected).merge_candidates[0].verification_status == "rejected"
    with pytest.raises(DomainError) as exc:
        review_candidates("v1", [ReviewDecision(candidate.candidate_id, "accept")], "teacher", base,
                          page_text={("A.pdf", 1): "A directed quantity"}, **kwargs)
    assert exc.value.code == "EVIDENCE_NOT_IN_TEXT"
    accepted = review_candidates("v1", [ReviewDecision(candidate.candidate_id, "accept")], "teacher", base,
                                page_text={("A.pdf", 1): "A directed quantity", ("B.pdf", 1): "Different meaning"}, **kwargs)
    assert len(accepted.points) == 1
    assert accepted.merge_candidates[0].verification_status == "verified"
    assert accepted.merge_candidates[0].reviewed_by == "teacher"
    assert len(base.points) == 2


def test_relation_collapse_blocks_auto_and_human_merge_without_losing_edges():
    base = KnowledgeBase(kb_version="v1", points=[_point("A"), _point("B")],
                         relations=[Relation(relation_id="R", type="derives_from", source_ids=["A"], target_id="B")])
    result = deduplicate_knowledge(base)
    assert len(result.points) == 2
    assert result.relations == base.relations
    with pytest.raises(DomainError) as exc:
        review_candidates("v1", [ReviewDecision(result.merge_candidates[0].candidate_id, "accept")], "teacher", result,
                          new_version="v2", catalog=_catalog(),
                          page_text={("A.pdf", 1): "A directed quantity", ("B.pdf", 1): "A directed quantity"})
    assert exc.value.code == "MERGE_RELATION_CONFLICT"
    assert result.relations == base.relations


def test_truncated_snippets_are_insufficient_for_automatic_equivalence():
    base = KnowledgeBase(kb_version="v1", points=[_point("A", snippet="x" * 80), _point("B", snippet="x" * 80)])
    result = deduplicate_knowledge(base)
    assert len(result.points) == 2
    assert len(result.merge_candidates) == 1


def test_math_operators_and_definition_only_match_are_not_auto_merged():
    base = KnowledgeBase(kb_version="v1", points=[_point("A", snippet="x < y"), _point("B", snippet="x > y")])
    assert len(deduplicate_knowledge(base).points) == 2
    base.points[1].name = "Other"
    base.points[1].snippet = base.points[0].snippet
    result = deduplicate_knowledge(base)
    assert len(result.points) == 2
    assert result.merge_candidates[0].match_basis == ["definition"]

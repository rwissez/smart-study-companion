"""knowledge 模块测试：全部使用自拟的短文本，不含教材原文。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from study_companion.knowledge.extract import PageRecord, normalize_text
from study_companion.knowledge.index import locate, neighbors, upstream_closure
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.project import project_to_catalog
from study_companion.knowledge.relations import validate_candidates
from study_companion.knowledge.targets import (
    generate_target_candidates,
    register_textbook_fragment,
    textbook_fragment_id,
    validate_target_candidates,
)
from study_companion.knowledge.terms import extract_knowledge
from study_companion.contracts import Catalog, Indicator, Material, RubricPoint, SourceLocator, Topic


def _loc(page: int) -> Locator:
    return Locator(source_file="X.pdf", file_page=page, printed_page=100 + page, chapter="第一章", section="1.1 示例")


def _kb() -> KnowledgeBase:
    pts = [
        KnowledgePoint(kp_id=f"K{i}", name=n, definition_locator=_loc(i))
        for i, n in enumerate(["甲概念", "甲的性质1", "乙概念", "丙概念"], start=1)
    ]
    return KnowledgeBase(kb_version="t", points=pts)


def test_normalize_fullwidth():
    assert normalize_text("１２３ａ＜３") == "123a<3"


def test_relation_shape_rules():
    with pytest.raises(ValidationError):
        Relation(relation_id="r", type="composed_of", source_ids=["K1"], target_id="K3")
    with pytest.raises(ValidationError):
        Relation(relation_id="r", type="has_property", source_ids=["K1", "K2"], target_id="K3")
    with pytest.raises(ValidationError):
        Relation(relation_id="r", type="derives_from", source_ids=["K1"], target_id="K1")
    ok = Relation(relation_id="r", type="composed_of", source_ids=["K1", "K2"], target_id="K3")
    assert ok.verification_status == "candidate"


def test_extract_new_terms_and_property_relation():
    text = "把满足条件的式子叫作甲概念．\n甲概念的性质1 两边同加一个数，结果不变．\n乙叫作丙概念．"
    pages = [PageRecord(1, text, "text")]
    kb = extract_knowledge(pages, {1: _loc(1)}, "T", "v")
    names = [p.name for p in kb.points]
    assert "甲概念" in names and "甲概念的性质1" in names
    rel = [r for r in kb.relations if r.type == "has_property"]
    assert len(rel) == 1 and rel[0].proposer == "rule"


def test_new_term_registered_once_on_first_page():
    pages = [PageRecord(1, "称为甲概念．", "text"), PageRecord(2, "再次称为甲概念．", "text")]
    kb = extract_knowledge(pages, {1: _loc(1), 2: _loc(2)}, "T", "v")
    assert len(kb.points) == 1 and kb.points[0].definition_locator.file_page == 1


def test_locate_exact_alias_substring():
    kb = _kb()
    kb.points[0].aliases.append("Alpha")
    assert locate(kb, "甲概念")[0].match == "exact"
    assert locate(kb, "alpha")[0].match == "alias"
    hits = locate(kb, "概念")
    assert {h.match for h in hits} == {"substring"} and len(hits) == 3
    h = locate(kb, "甲概念")[0]
    assert (h.unit, h.file_page, h.printed_page, h.verification_status) == ("第一章", 1, 101, "unverified")
    assert locate(kb, "不存在") == []


def test_validator_rejections():
    kb = _kb()
    pages = {1: "原文里有 甲 与 乙 的类比句"}
    good = Relation(relation_id="a", type="derives_from", source_ids=["K1"], target_id="K3",
                    evidence="甲与乙的类比", evidence_file_page=1, proposer="agent")
    bad_sub = good.model_copy(update={"relation_id": "b", "target_id": "K4", "evidence": "编造的句子"})
    no_ev = Relation(relation_id="c", type="derives_from", source_ids=["K1"], target_id="K4", proposer="agent")
    ghost = Relation(relation_id="d", type="has_property", source_ids=["KX"], target_id="K2")
    acc, rej = validate_candidates(kb, [good, bad_sub, no_ev, ghost], pages)
    assert [r.relation_id for r in acc] == ["a"]
    assert {r.relation_id: r.code for r in rej} == {
        "b": "EVIDENCE_NOT_IN_TEXT", "c": "EVIDENCE_REQUIRED", "d": "KP_NOT_FOUND"}


def test_validator_cycle_and_duplicate():
    kb = _kb()
    kb.relations.append(Relation(relation_id="e1", type="derives_from", source_ids=["K1"], target_id="K3"))
    cyc = Relation(relation_id="e2", type="derives_from", source_ids=["K3"], target_id="K1")
    dup = Relation(relation_id="e3", type="derives_from", source_ids=["K1"], target_id="K3")
    acc, rej = validate_candidates(kb, [cyc, dup], {})
    assert acc == [] and {r.code for r in rej} == {"CYCLE", "DUPLICATE"}


def test_neighbors_and_upstream():
    kb = _kb()
    kb.relations += [
        Relation(relation_id="1", type="has_property", source_ids=["K1"], target_id="K2"),
        Relation(relation_id="2", type="composed_of", source_ids=["K2", "K3"], target_id="K4"),
    ]
    assert len(neighbors(kb, "K2")) == 2
    assert len(neighbors(kb, "K2", {"composed_of"})) == 1
    assert set(upstream_closure(kb, "K4")) == {"K1", "K2", "K3"}
    assert upstream_closure(kb, "K4", only_verified=True) == []


def test_projection_uses_confirmed_relation_mapping():
    kb = _kb()
    for point, indicator in zip(kb.points, ["I1", "I2", "I3", "I4"]):
        point.indicator_ids = [indicator]
        point.verification_status = "verified"
    kb.relations = [
        Relation(relation_id="derive", type="derives_from", source_ids=["K1"], target_id="K3",
                 verification_status="verified"),
        Relation(relation_id="compose", type="composed_of", source_ids=["K1", "K2"], target_id="K4",
                 verification_status="verified"),
        Relation(relation_id="special", type="special_case", source_ids=["K3"], target_id="K4",
                 verification_status="verified"),
        Relation(relation_id="property", type="has_property", source_ids=["K1"], target_id="K2",
                 verification_status="verified"),
        Relation(relation_id="candidate", type="derives_from", source_ids=["K2"], target_id="K3"),
    ]
    base = Catalog(
        version="base",
        topics=[Topic(topic_id="T", title="测试主题")],
        indicators=[Indicator(indicator_id=f"I{i}", topic_id="T", description=f"指标{i}") for i in range(1, 5)],
        materials=[],
        links=[],
    )
    result = project_to_catalog(kb, base, "projected")
    links = {(x.source_id, x.target_id, x.relation) for x in result.catalog.links}
    assert ("I1", "I3", "prerequisite") in links  # 衍生：前置
    assert ("I1", "I4", "prerequisite") in links  # 合并创造：前置
    assert ("I2", "I4", "prerequisite") in links
    assert ("I3", "I4", "supports") in links  # 特例：支持
    assert ("I1", "I2", "supports") not in links  # 从属不映射
    assert result.report.candidate_relation_ids == ["candidate"]
    assert "property" in result.report.skipped_relation_ids
    assert len(result.catalog.materials) == 4
    assert result.catalog.version == "projected"


def test_target_candidates_are_conservative_and_explainable():
    kb = _kb()
    kb.points[0].aliases = ["AlphaConcept"]
    kb.points[0].snippet = "AlphaConcept meets condition"
    kb.points[0].topic_ids = ["T1"]
    kb.points[0].indicator_ids = ["I1"]
    catalog = Catalog(
        version="cat-v1",
        topics=[Topic(topic_id="T1", title="AlphaConcept")],
        indicators=[Indicator(indicator_id="I1", topic_id="T1", description="AlphaConcept meets condition")],
        materials=[Material(
            material_id="M1", material_type="question", topic_ids=["T1"], indicator_ids=["I1"],
            content="Please explain AlphaConcept", source_locator=SourceLocator(source_file="x", pdf_page=1, verification_status="unverified"),
            rubric_points=[RubricPoint(point_id="R1", description="AlphaConcept", max_score=1)],
        )],
        links=[],
    )
    candidates = generate_target_candidates(kb, catalog)
    assert {(c.source_id, c.target_type, c.target_ids[0]) for c in candidates} >= {
        ("K1", "topic", "T1"), ("K1", "indicator", "I1"),
        ("cat-v1/M1", "knowledge_point", "K1"), ("cat-v1/M1/R1", "knowledge_point", "K1"),
    }
    assert all(c.verification_status == "unverified" and c.reason and c.keywords for c in candidates)


def test_textbook_fragment_registration_is_deterministic_and_manifest_bound():
    kb = _kb()
    fragment = register_textbook_fragment("book.pdf", 2, "甲概念满足条件", "K1")
    same = register_textbook_fragment("book.pdf", 2, "甲概念满足条件", "K1")
    assert fragment.candidate_id == same.candidate_id
    assert textbook_fragment_id("book", 1, "alpha  beta") == textbook_fragment_id("book", 1, "alpha\n beta")
    assert fragment.source_id == textbook_fragment_id("book.pdf", 2, "甲概念满足条件")
    accepted, rejected = validate_target_candidates(
        kb, Catalog(version="cat", topics=[], indicators=[], materials=[], links=[]),
        [fragment], {("book.pdf", 2): "本页包含甲概念满足条件。"},
    )
    assert accepted == [fragment]
    assert rejected == []


def test_textbook_fragment_validation_rejects_wrong_id_and_unmanifested_page():
    kb = _kb()
    catalog = Catalog(version="cat", topics=[], indicators=[], materials=[], links=[])
    fragment = register_textbook_fragment("book.pdf", 2, "甲概念满足条件", "K1")
    wrong = fragment.model_copy(update={"source_id": "FRAG-invalid"})
    accepted, rejected = validate_target_candidates(kb, catalog, [wrong], {("book.pdf", 2): "甲概念满足条件"})
    assert accepted == []
    assert rejected[0].code == "FRAGMENT_ID_MISMATCH"
    accepted, rejected = validate_target_candidates(kb, catalog, [fragment], {("book.pdf", 1): "甲概念满足条件"})
    assert accepted == []
    assert rejected[0].code == "SOURCE_NOT_FOUND"


def test_target_candidate_validation_reports_missing_ids_duplicates_and_auto_verified():
    kb = _kb()
    catalog = Catalog(version="cat-v1", topics=[Topic(topic_id="T1", title="主题")], indicators=[], materials=[], links=[])
    valid = Annotation(candidate_id="c1", source_type="knowledge_point", source_id="K1", target_type="topic", target_ids=["T1"], proposer="rule")
    duplicate = valid.model_copy(update={"candidate_id": "c2"})
    missing = valid.model_copy(update={"candidate_id": "c3", "target_ids": ["T404"]})
    auto_verified = valid.model_copy(update={"candidate_id": "c4", "verification_status": "verified"})
    accepted, rejected = validate_target_candidates(kb, catalog, [valid, duplicate, missing, auto_verified])
    assert [item.candidate_id for item in accepted] == ["c1"]
    assert {item.code for item in rejected} == {"DUPLICATE", "TOPIC_NOT_FOUND", "AUTO_VERIFIED"}


def test_projection_skips_missing_indicator_mapping():
    kb = _kb()
    kb.points[0].indicator_ids = ["I1"]
    kb.relations = [
        Relation(relation_id="r", type="derives_from", source_ids=["K1"], target_id="K3",
                 verification_status="verified")
    ]
    base = Catalog(
        version="base",
        topics=[Topic(topic_id="T", title="测试主题")],
        indicators=[Indicator(indicator_id="I1", topic_id="T", description="指标")],
        materials=[],
        links=[],
    )
    result = project_to_catalog(kb, base, "v2")
    assert result.catalog.links == []
    assert result.report.skipped_relation_ids == ["r"]
    assert "缺少知识点到指标的标注" in result.report.warnings[0]

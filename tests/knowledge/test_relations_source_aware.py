from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.relations import validate_candidates


def _kb() -> KnowledgeBase:
    return KnowledgeBase(
        kb_version="v1",
        points=[
            KnowledgePoint(
                kp_id="K1",
                name="甲",
                definition_locator=Locator(source_file="a.pdf", file_page=1),
            ),
            KnowledgePoint(
                kp_id="K2",
                name="乙",
                definition_locator=Locator(source_file="a.pdf", file_page=1),
            ),
        ],
    )


def _relation(relation_id: str = "r", source_file: str | None = "a.pdf") -> Relation:
    return Relation(
        relation_id=relation_id,
        type="derives_from",
        source_ids=["K1"],
        target_id="K2",
        evidence="甲乙关系",
        evidence_file_page=1,
        source_file=source_file,
        proposer="agent",
    )


def test_source_qualified_page_text_rejects_matching_text_from_wrong_file():
    relation = _relation()
    accepted, rejected = validate_candidates(
        _kb(),
        [relation],
        {("b.pdf", 1): "甲乙关系", ("a.pdf", 1): "其他内容"},
    )

    assert accepted == []
    assert [(item.relation_id, item.code) for item in rejected] == [
        ("r", "EVIDENCE_NOT_IN_TEXT")
    ]


def test_source_qualified_page_text_accepts_tuple_and_nested_shapes():
    tuple_result = validate_candidates(_kb(), [_relation()], {("a.pdf", 1): "甲乙关系"})
    nested_result = validate_candidates(_kb(), [_relation()], {"a.pdf": {1: "甲乙关系"}})

    assert [item.relation_id for item in tuple_result[0]] == ["r"]
    assert [item.relation_id for item in nested_result[0]] == ["r"]


def test_legacy_page_only_text_remains_supported_without_source_file():
    relation = _relation(source_file=None)
    accepted, rejected = validate_candidates(_kb(), [relation], {1: "甲乙关系"})

    assert [item.relation_id for item in accepted] == ["r"]
    assert rejected == []


def test_relation_ids_must_be_unique_across_existing_and_candidates():
    kb = _kb()
    kb.relations = [_relation("existing")]
    duplicate_existing = _relation("existing")
    duplicate_candidate = _relation("new")

    accepted, rejected = validate_candidates(
        kb,
        [duplicate_existing, duplicate_candidate, _relation("new")],
        {("a.pdf", 1): "甲乙关系"},
    )

    assert accepted == []
    assert [(item.relation_id, item.code) for item in rejected] == [
        ("existing", "RELATION_ID_DUPLICATE"),
        ("new", "DUPLICATE"),
        ("new", "RELATION_ID_DUPLICATE"),
    ]


def test_source_file_requires_a_file_page():
    relation = _relation().model_copy(update={"evidence_file_page": None})

    accepted, rejected = validate_candidates(_kb(), [relation], {})

    assert accepted == []
    assert [(item.relation_id, item.code) for item in rejected] == [
        ("r", "SOURCE_PAGE_REQUIRED")
    ]

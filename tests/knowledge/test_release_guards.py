"""Regression guards for reviewed projection and legacy annotations."""

import pytest

from study_companion.contracts import Catalog, DomainError, Indicator, Material, SourceLocator, Topic
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator, Relation
from study_companion.knowledge.project import project_to_catalog
from study_companion.knowledge.store import KnowledgeStore, source_manifest_entry


def _catalog():
    return Catalog(version="cat", topics=[Topic(topic_id="T", title="Topic")],
        indicators=[Indicator(indicator_id="I1", topic_id="T", description="One"),
                    Indicator(indicator_id="I2", topic_id="T", description="Two")],
        materials=[Material(material_id="M1", material_type="card", topic_ids=[], indicator_ids=[],
            content="Card", source_locator=SourceLocator(source_file="book", verification_status="verified"))], links=[])


def _point(kp_id, indicator):
    return KnowledgePoint(kp_id=kp_id, name=kp_id, snippet="definition", indicator_ids=[indicator],
        verification_status="verified", definition_locator=Locator(source_file="book", file_page=1, verification_status="verified"))


@pytest.mark.parametrize("state", ["unverified", "rejected", "unverified_locator", "missing"])
def test_projection_excludes_unreviewed_and_missing_endpoints(state):
    first, second = _point("K1", "I1"), _point("K2", "I2")
    if state == "unverified_locator":
        second.definition_locator.verification_status = "unverified"
    elif state != "missing":
        second.verification_status = state
    kb = KnowledgeBase(kb_version="v", points=[first] if state == "missing" else [first, second], relations=[
        Relation(relation_id="R", type="derives_from", source_ids=["K1"], target_id="K2", verification_status="verified")])
    result = project_to_catalog(kb, _catalog(), "next")
    assert [m.material_id for m in result.catalog.materials] == ["M1", "kp:K1"]
    assert result.catalog.links == []
    assert "R" in result.report.skipped_relation_ids


@pytest.mark.parametrize("status, material, kp, error", [
    ("verified", "M1", "K1", None),
    ("unverified", "M1", "K1", "UNVERIFIED_ANNOTATION"),
    ("verified", "missing", "K1", "TARGET_VALIDATION_FAILED"),
    ("verified", "M1", "missing", "TARGET_VALIDATION_FAILED"),
])
def test_publication_preserves_and_validates_legacy_annotations(tmp_path, status, material, kp, error):
    source = tmp_path / "book.txt"
    source.write_text("definition", encoding="utf-8")
    entry = source_manifest_entry(tmp_path, source, source_id="book", page_count=1,
        extraction_method="text", extraction_version="1")
    annotation = Annotation(material_id=material, kp_ids=[kp], verification_status=status)
    kb = KnowledgeBase(kb_version="draft", points=[_point("K1", "I1")], annotations=[annotation])
    store = KnowledgeStore(tmp_path)
    store.save_draft("ds", "cat", kb, source_files=[entry])
    if error:
        with pytest.raises(DomainError) as exc:
            store.publish_verified("ds", "cat", "draft", catalog=_catalog())
        assert exc.value.code == error
        assert store.current_version("ds", "cat") == "draft"
    else:
        published = store.publish_verified("ds", "cat", "draft", catalog=_catalog())
        assert published.annotations == [annotation]
        assert store.load_version("ds", "cat")[0].annotations == [annotation]

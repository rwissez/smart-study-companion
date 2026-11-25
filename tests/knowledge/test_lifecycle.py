"""Synthetic local human review -> immutable publication acceptance."""

import pytest

from study_companion.contracts import Catalog, DomainError, Topic
from study_companion.knowledge.models import Annotation, KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.review import ReviewDecision
from study_companion.knowledge.store import KnowledgeStore, source_manifest_entry


def _draft(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("A polynomial is an expression.", encoding="utf-8")
    manifest = source_manifest_entry(tmp_path, source, source_id="source", page_count=1,
                                     extraction_method="text", extraction_version="1")
    point = KnowledgePoint(kp_id="KP1", name="Polynomial", snippet="A polynomial is an expression.",
                           definition_locator=Locator(source_file="source", file_page=1))
    annotation = Annotation(candidate_id="A1", source_type="knowledge_point", source_id="KP1",
                            target_type="topic", target_ids=["T1"], evidence=point.snippet, proposer="rule")
    store = KnowledgeStore(tmp_path)
    store.save_draft("ds", "cat", KnowledgeBase(kb_version="v1", points=[point], annotations=[annotation]),
                     source_files=[manifest])
    catalog = Catalog(version="cat", topics=[Topic(topic_id="T1", title="Algebra")],
                      indicators=[], materials=[], links=[])
    return store, catalog, source


def test_review_publish_preserves_ancestor_bytes(tmp_path):
    store, catalog, _ = _draft(tmp_path)
    original = tmp_path / "knowledge/ds/cat/v1/knowledge_base.json"
    before = original.read_bytes()
    reviewed = store.review_candidates("ds", "cat", "v1",
        [ReviewDecision("KP1", "accept"), ReviewDecision("A1", "accept")], "teacher",
        new_version="v2", catalog=catalog)
    assert reviewed.points[0].topic_ids == ["T1"]
    published = store.publish_verified("ds", "cat", "v2", catalog=catalog, new_version="v3")
    assert published.status == "verified"
    assert published.parent_version == "v2"
    assert store.current_version("ds", "cat") == "v3"
    assert before == original.read_bytes()
    assert not list((tmp_path / "knowledge").rglob("pages.json"))


def test_hash_change_and_pointer_failure_preserve_history(tmp_path, monkeypatch):
    store, catalog, source = _draft(tmp_path)
    original = (tmp_path / "knowledge/ds/cat/v1/knowledge_base.json").read_bytes()
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        store.review_candidates("ds", "cat", "v1", [ReviewDecision("KP1", "accept")], "teacher",
                                new_version="v2", catalog=catalog)
    assert exc.value.code == "SOURCE_HASH_MISMATCH"
    assert store.current_version("ds", "cat") == "v1"
    def fail(*args):
        raise OSError("simulated pointer failure")
    monkeypatch.setattr(store, "_atomic_json", fail)
    with pytest.raises(OSError):
        store.save_draft("ds", "cat", KnowledgeBase(kb_version="v2"), parent_version="v1")
    assert store.current_version("ds", "cat") == "v1"
    assert (tmp_path / "knowledge/ds/cat/v1/knowledge_base.json").read_bytes() == original


def test_publication_rejects_unbound_sources_and_draft_status_bypass(tmp_path):
    store = KnowledgeStore(tmp_path)
    catalog = Catalog(version="cat", topics=[], indicators=[], materials=[], links=[])
    kb = KnowledgeBase(kb_version="v1", points=[KnowledgePoint(kp_id="KP", name="Concept",
        verification_status="verified", definition_locator=Locator(source_file="absent", file_page=1,
                                                                    verification_status="verified"))])
    store.save_draft("ds", "cat", kb)
    with pytest.raises(DomainError) as exc:
        store.publish_verified("ds", "cat", "v1", catalog=catalog)
    assert exc.value.code == "SOURCE_NOT_FOUND"
    with pytest.raises(DomainError) as exc:
        store.save_draft("ds", "cat", kb.model_copy(update={"kb_version": "v2", "status": "verified"}))
    assert exc.value.code == "DRAFT_ONLY"

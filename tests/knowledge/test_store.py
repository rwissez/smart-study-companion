"""Focused contract tests for versioned knowledge snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from study_companion.contracts import Catalog, DomainError
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.store import (
    KnowledgeStore,
    source_manifest_entry,
)


def _kb(version: str, *, created_at: str | None = "2026-01-01T00:00:00+00:00") -> KnowledgeBase:
    return KnowledgeBase(kb_version=version, created_at=created_at)


def _error_code(error: pytest.ExceptionInfo[DomainError]) -> str:
    return error.value.code


def test_save_and_load_round_trip_preserves_snapshot_and_manifest(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    source = tmp_path / "sources" / "chapter.txt"
    source.parent.mkdir()
    source.write_text("short source", encoding="utf-8")
    entry = source_manifest_entry(
        tmp_path,
        source,
        source_id="chapter",
        page_count=1,
        extraction_method="text",
        extraction_version="1",
    )

    saved = store.save_draft("dataset", "catalog", _kb("v1"), source_files=[entry])
    loaded, manifest = store.load_version("dataset", "catalog", verify_sources=True)

    assert saved.kb_version == loaded.kb_version == "v1"
    assert loaded.status == manifest.status == "draft"
    assert manifest.dataset_id == "dataset"
    assert manifest.catalog_version == "catalog"
    assert manifest.source_files == [entry]
    assert store.current_version("dataset", "catalog") == "v1"


def test_saved_snapshots_are_immutable_in_memory_and_on_disk(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    original = _kb("v1")
    store.save_draft("dataset", "catalog", original)

    original.source_summary["changed"] = True
    loaded, _ = store.load_version("dataset", "catalog", "v1")
    loaded.source_summary["changed_after_load"] = True
    reloaded, _ = store.load_version("dataset", "catalog", "v1")

    assert original.status == "draft"
    assert loaded.source_summary == {"changed_after_load": True}
    assert reloaded.source_summary == {}

    with pytest.raises(DomainError) as exc:
        store.save_draft("dataset", "catalog", _kb("v1"))
    assert _error_code(exc) == "KB_VERSION_EXISTS"


def test_publish_creates_new_verified_version_and_preserves_draft(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text("Definition", encoding="utf-8")
    entry = source_manifest_entry(tmp_path, source, source_id="book", page_count=1,
                                  extraction_method="text", extraction_version="1")
    point = KnowledgePoint(kp_id="K1", name="Concept", snippet="Definition", definition_locator=Locator(source_file="book", file_page=1, verification_status="verified"), verification_status="verified")
    store.save_draft("dataset", "catalog", KnowledgeBase(kb_version="draft", points=[point]), source_files=[entry])
    catalog = Catalog(version="catalog", topics=[], indicators=[], materials=[], links=[])

    published = store.publish_verified("dataset", "catalog", "draft", catalog=catalog, new_version="published")
    original, original_manifest = store.load_version("dataset", "catalog", "draft")
    loaded, manifest = store.load_version("dataset", "catalog", "published")

    assert published.status == loaded.status == manifest.status == "verified"
    assert original.status == original_manifest.status == "draft"
    assert original.parent_version is None
    assert loaded.parent_version == "draft"
    assert store.current_version("dataset", "catalog") == "published"


def test_unreviewed_knowledge_cannot_publish(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    store.save_draft("dataset", "catalog", KnowledgeBase(kb_version="draft", points=[KnowledgePoint(kp_id="K1", name="待核对", definition_locator=Locator(source_file="book.pdf", file_page=1))]))
    with pytest.raises(DomainError) as exc:
        store.publish_verified("dataset", "catalog", "draft", catalog=Catalog(version="catalog", topics=[], indicators=[], materials=[], links=[]))
    assert _error_code(exc) == "UNVERIFIED_KNOWLEDGE"


def test_source_manifest_records_relative_path_and_sha256(tmp_path: Path) -> None:
    source = tmp_path / "nested" / "source.pdf"
    source.parent.mkdir()
    content = b"source bytes"
    source.write_bytes(content)

    entry = source_manifest_entry(
        tmp_path,
        source,
        source_id="source-1",
        page_count=2,
        extraction_method="pdf",
        extraction_version="2",
    )

    assert entry.relative_path == "nested/source.pdf"
    assert entry.sha256 == hashlib.sha256(content).hexdigest()
    assert KnowledgeStore(tmp_path).resolve_source(entry) == source.resolve()


def test_source_resolution_rejects_hash_changes_and_paths_outside_root(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    entry = source_manifest_entry(
        tmp_path,
        source,
        source_id="source-1",
        page_count=1,
        extraction_method="text",
        extraction_version="1",
    )
    source.write_text("after", encoding="utf-8")

    with pytest.raises(DomainError) as exc:
        KnowledgeStore(tmp_path).resolve_source(entry)
    assert _error_code(exc) == "SOURCE_HASH_MISMATCH"

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        source_manifest_entry(
            tmp_path,
            outside,
            source_id="outside",
            page_count=1,
            extraction_method="text",
            extraction_version="1",
        )
    assert _error_code(exc) == "INVALID_SOURCE_PATH"

    escaped = entry.model_copy(update={"relative_path": "../outside.txt"})
    with pytest.raises(DomainError) as exc:
        KnowledgeStore(tmp_path).resolve_source(escaped)
    assert _error_code(exc) == "INVALID_SOURCE_PATH"


def test_parent_snapshot_ingest_combines_sources_and_deduplicates(tmp_path: Path, monkeypatch) -> None:
    from study_companion.knowledge.extract import PageRecord
    from study_companion.knowledge.models import MergeCandidate
    from study_companion.knowledge import ingest
    from study_companion.knowledge.dedup import deduplicate_knowledge

    first = tmp_path / "one" / "book.pdf"
    second = tmp_path / "two" / "book.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first_entry = source_manifest_entry(tmp_path, first, source_id="one/book.pdf", page_count=1,
                                       extraction_method="text", extraction_version="1")
    point = KnowledgePoint(kp_id="KP-old", name="Concept", snippet="A concept definition",
                           definition_locator=Locator(source_file="one/book.pdf", file_page=1))
    store = KnowledgeStore(tmp_path)
    store.save_draft("ds", "cat", KnowledgeBase(kb_version="parent", points=[point]),
                     source_files=[first_entry])
    parent_bytes = (tmp_path / "knowledge/ds/cat/parent/knowledge_base.json").read_bytes()
    imported_point = KnowledgePoint(kp_id="KP-new", name="Concept", snippet="A concept definition",
                                    definition_locator=Locator(source_file="book.pdf", file_page=1))
    imported_point.source_locators = [imported_point.definition_locator.model_copy()]
    extracted = KnowledgeBase(kb_version="next", points=[imported_point],
                              merge_candidates=[MergeCandidate(candidate_id="MERGE-import", source_id="KP-new",
                                  target_id="KP-other", reason="Existing proposal")])
    monkeypatch.setattr(ingest, "ingest_textbook", lambda *args, **kwargs: (extracted.model_copy(deep=True),
                        [PageRecord(1, "A concept definition", "text")]))
    dedup_calls = []
    def dedup(kb):
        dedup_calls.append(kb.model_copy(deep=True))
        return deduplicate_knowledge(kb)
    monkeypatch.setattr("study_companion.knowledge.dedup.deduplicate_knowledge", dedup)

    result = ingest.ingest_textbook_snapshot(second, data_root=tmp_path, dataset_id="ds",
        catalog_version="cat", prefix="book", kb_version="next", parent_version="parent")
    _, manifest = store.load_version("ds", "cat", "next")
    assert [source.source_id for source in manifest.source_files] == ["one/book.pdf", "two/book.pdf"]
    assert [point.kp_id for point in result.points] == ["KP-old"]
    assert {loc.source_file for loc in result.points[0].source_locators} == {"one/book.pdf", "two/book.pdf"}
    assert any(candidate.candidate_id == "MERGE-import" for candidate in result.merge_candidates)
    assert len(dedup_calls) == 1
    assert (tmp_path / "knowledge/ds/cat/parent/knowledge_base.json").read_bytes() == parent_bytes


def test_resolver_reads_only_referenced_pdf_pages(tmp_path: Path, monkeypatch) -> None:
    from study_companion.knowledge.extract import PageRecord
    from study_companion.knowledge.store import KnowledgeManifest

    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")
    entry = source_manifest_entry(tmp_path, source, source_id="book", page_count=20,
                                  extraction_method="text", extraction_version="1")
    manifest = KnowledgeManifest(dataset_id="d", catalog_version="c", kb_version="v",
                                 created_at="now", source_files=[entry])
    kb = KnowledgeBase(kb_version="v", points=[KnowledgePoint(
        kp_id="k", name="n", definition_locator=Locator(source_file="book", file_page=7))])
    requested = []
    def read(self, path, pages):
        requested.extend(pages)
        return [PageRecord(file_page=7, text="definition", method="text")]
    monkeypatch.setattr("study_companion.knowledge.store.PyPdfExtractor.extract", read)
    assert KnowledgeStore(tmp_path)._manifest_page_text(manifest, kb)[("book", 7)] == "definition"
    assert requested == [7]


def test_explicit_legacy_import_is_draft_and_preserves_original(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("definition", encoding="utf-8")
    entry = source_manifest_entry(tmp_path, source, source_id="book", page_count=1,
                                  extraction_method="text", extraction_version="1")
    legacy = tmp_path / "old.json"
    point = KnowledgePoint(kp_id="k", name="n", snippet="definition", verification_status="verified",
                           definition_locator=Locator(source_file="book", file_page=1, verification_status="verified"))
    original = KnowledgeBase(kb_version="old", status="verified", points=[point]).model_dump_json()
    legacy.write_text(original, encoding="utf-8")
    store = KnowledgeStore(tmp_path)
    imported = store.import_legacy_json("dataset", "catalog", legacy, kb_version="new", source_files=[entry])
    assert imported.status == "draft"
    assert imported.points[0].verification_status == "unverified"
    assert imported.points[0].definition_locator.verification_status == "unverified"
    assert legacy.read_text(encoding="utf-8") == original
    assert store.load_version("dataset", "catalog", "new")[0] == imported
    with pytest.raises(DomainError):
        store.load_version("other", "catalog", "new")


def test_dataset_and_catalog_versions_are_isolated(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    store.save_draft("dataset-a", "catalog", _kb("v1"))
    store.save_draft("dataset-b", "catalog", _kb("v1"))
    store.save_draft("dataset-a", "other-catalog", _kb("v1"))

    assert store.current_version("dataset-a", "catalog") == "v1"
    assert store.current_version("dataset-b", "catalog") == "v1"
    assert store.current_version("dataset-a", "other-catalog") == "v1"
    assert store.load_version("dataset-a", "catalog")[0].kb_version == "v1"
    assert store.load_version("dataset-b", "catalog")[0].kb_version == "v1"


@pytest.mark.parametrize(
    "pointer, expected",
    [
        ("not-json", "KB_POINTER_INVALID"),
        (json.dumps({"wrong": "v1"}), "KB_POINTER_INVALID"),
        (json.dumps({"kb_version": "../escape"}), "INVALID_KB_VERSION"),
    ],
)
def test_invalid_current_pointer_fails_closed(
    tmp_path: Path, pointer: str, expected: str
) -> None:
    store = KnowledgeStore(tmp_path)
    pointer_path = tmp_path / "knowledge" / "dataset" / "catalog" / "current.json"
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text(pointer, encoding="utf-8")

    with pytest.raises(DomainError) as exc:
        store.current_version("dataset", "catalog")
    assert _error_code(exc) == expected

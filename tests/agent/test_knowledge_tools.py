from __future__ import annotations

from pathlib import Path

import pytest

from study_companion.agent.knowledge_tools import build_knowledge_registry
from study_companion.agent.tasks import get_task
from study_companion.agent.types import ToolCall
from study_companion.contracts import Catalog, DomainError, Indicator, Topic
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.store import KnowledgeStore
from study_companion.tools.b_analysis import ServerContext


def _context(dataset_id: str = "dataset-a", catalog_version: str = "cat-a") -> ServerContext:
    return ServerContext("", dataset_id, catalog_version, frozenset(), None, None)


def _catalog(version: str = "cat-a") -> Catalog:
    return Catalog(version=version, topics=[Topic(topic_id="T1", title="Algebra")],
                   indicators=[Indicator(indicator_id="I1", topic_id="T1", description="Operations")],
                   materials=[], links=[])


def test_registry_is_task_allowlisted_read_only_and_returns_bound_evidence(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    store.save_draft("dataset-a", "cat-a", KnowledgeBase(kb_version="kb-a", points=[
        KnowledgePoint(kp_id="K1", name="Polynomial", aliases=["Poly"], snippet="Short definition", topic_ids=["T1"],
                       indicator_ids=["I1"], verification_status="verified",
                       definition_locator=Locator(source_file="book.pdf", file_page=4, verification_status="verified"))
    ]))
    task = get_task("knowledge_lookup")
    registry = build_knowledge_registry(_context(), "teacher", task, tmp_path, _catalog())

    assert {schema["function"]["name"] for schema in registry.schemas()} == set(task.tools)
    result = registry.call(ToolCall(
        call_id="call-1", name="locate_knowledge", arguments={"keyword": "Poly"}
    ))
    assert result["data"][0]["kp_id"] == "K1"
    assert result["data"][0]["kb_version"] == "kb-a"
    assert result["evidence_refs"] == [{"kind": "curriculum", "id": "K1", "catalog_version": "cat-a"}]
    assert "_call_id" not in result
    with pytest.raises(DomainError) as exc:
        registry.call(ToolCall(
            call_id="call-2", name="list_knowledge_candidates", arguments={}
        ))
    assert exc.value.code == "TOOL_NOT_ALLOWED"


def test_registry_rejects_mismatched_catalog_and_scopes_snapshot(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    store.save_draft("dataset-a", "cat-a", KnowledgeBase(kb_version="one"))
    store.save_draft("dataset-b", "cat-a", KnowledgeBase(kb_version="two"))
    with pytest.raises(DomainError) as exc:
        build_knowledge_registry(_context(), "teacher", get_task("knowledge_lookup"), tmp_path, _catalog("cat-b"))
    assert exc.value.code == "CONTEXT_MISMATCH"
    registry = build_knowledge_registry(_context("dataset-b"), "teacher", get_task("knowledge_lookup"), tmp_path, _catalog())
    assert "get_knowledge_detail" in {schema["function"]["name"] for schema in registry.schemas()}


def test_teacher_merge_category_is_read_only(tmp_path: Path):
    KnowledgeStore(tmp_path).save_draft("dataset-a", "cat-a", KnowledgeBase(kb_version="one"))
    registry = build_knowledge_registry(_context(), "teacher", get_task("relation_review"), tmp_path, _catalog())
    result = registry.call(ToolCall(call_id="merge-list", name="list_knowledge_candidates", arguments={"category": "merge"}))
    assert result["data"]["items"] == []
    assert result["evidence_refs"] == []
    assert {item["function"]["name"] for item in registry.schemas()} == {"list_knowledge_candidates"}

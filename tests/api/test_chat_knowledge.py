from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider
from study_companion.db import models
from study_companion.contracts import Catalog
from study_companion.knowledge.models import KnowledgeBase, KnowledgePoint, Locator
from study_companion.knowledge.store import KnowledgeStore, source_manifest_entry


def _token(client: TestClient, role: str, dataset_id: str, student_id: str | None = None) -> str:
    payload = {"role": role, "dataset_id": dataset_id}
    if student_id:
        payload["student_id"] = student_id
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _knowledge(data_root, dataset_id: str, catalog_version: str, kb_version: str, *, status="draft") -> None:
    point = KnowledgePoint(kp_id=f"K-{kb_version}", name="Polynomial", aliases=["Poly"], snippet="Definition",
                           verification_status="verified", definition_locator=Locator(
                               source_file="book.pdf", file_page=2, verification_status="verified"))
    kb = KnowledgeBase(kb_version=kb_version, status=status, points=[point])
    KnowledgeStore(data_root).save_draft(dataset_id, catalog_version, kb)
    if status == "verified":
        library = data_root / "knowledge" / dataset_id / catalog_version / kb_version
        snapshot = json.loads((library / "knowledge_base.json").read_text(encoding="utf-8"))
        snapshot["status"] = "verified"
        (library / "knowledge_base.json").write_text(json.dumps(snapshot), encoding="utf-8")
        manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
        manifest["status"] = "verified"
        (library / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _catalog(session, dataset_id: str, version: str) -> None:
    session.add(models.Catalog(version=version, dataset_id=dataset_id, content_hash="0" * 64,
                               created_at="2026-01-01T00:00:00+00:00",
                               payload_json={"topics": [], "links": []}))
    session.commit()


def _provider(handler):
    provider = OpenAICompatibleProvider("http://knowledge.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://knowledge.test/v1")
    return provider


def test_chat_knowledge_lookup_without_run_uses_dataset_catalog_and_traces_refs(
    client: TestClient, seeded: dict, session, app_env, monkeypatch
) -> None:
    dataset_id = seeded["dataset_id"]
    catalog = session.query(models.Catalog).filter_by(dataset_id=dataset_id).first()
    _knowledge(app_env.parent / "data", dataset_id, catalog.version, "kb-http")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            assert "run_id=" not in body["messages"][0]["content"]
            assert {item["function"]["name"] for item in body["tools"]} == {"locate_knowledge", "get_knowledge_detail"}
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
                "id": "find-1", "type": "function", "function": {
                    "name": "locate_knowledge", "arguments": '{"keyword":"Poly"}'
                }
            }]}}]})
        tool_result = json.loads(body["messages"][-1]["content"])
        assert tool_result["data"][0]["kp_id"] == "K-kb-http"
        return httpx.Response(200, json={"choices": [{"message": {"content": "Found the knowledge point."}}]})

    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: _provider(handler))
    token = _token(client, "teacher", dataset_id)
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "task_type": "knowledge_lookup", "messages": [{"role": "user", "content": "Find Poly"}]
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tool_trace"][0]["call_id"] == "find-1"
    assert data["tool_trace"][0]["evidence_refs"] == [{
        "kind": "curriculum", "id": "K-kb-http", "catalog_version": catalog.version
    }]


def test_student_chat_filters_unverified_points_and_denies_draft_kb(
    client: TestClient, seeded: dict, session, app_env, monkeypatch
) -> None:
    dataset_id = seeded["dataset_id"]
    catalog = session.query(models.Catalog).filter_by(dataset_id=dataset_id).first()
    store = KnowledgeStore(app_env.parent / "data")
    kb = KnowledgeBase(kb_version="kb-mixed", status="draft", points=[
        KnowledgePoint(kp_id="K-public", name="Public", verification_status="verified", definition_locator=Locator(source_file="book.txt", file_page=1, verification_status="verified"))
    ])
    source = app_env.parent / "data" / "book.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Definition", encoding="utf-8")
    source_entry = source_manifest_entry(app_env.parent / "data", source, source_id="book", page_count=1,
                                         extraction_method="text", extraction_version="1")
    store.save_draft(dataset_id, catalog.version, kb, source_files=[source_entry])
    store.publish_verified(dataset_id, catalog.version, "kb-mixed", catalog=Catalog(version=catalog.version, topics=[], indicators=[], materials=[], links=[]), page_text={("book", 1): "Definition"}, new_version="kb-public")
    path = store._library(dataset_id, catalog.version) / "kb-public" / "knowledge_base.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["points"].append(KnowledgePoint(kp_id="K-hidden", name="Hidden", verification_status="unverified", definition_locator=Locator(source_file="book.txt", file_page=1)).model_dump(mode="json"))
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
                "id": "find-student", "type": "function", "function": {
                    "name": "locate_knowledge", "arguments": '{"keyword":"Public"}'
                }
            }]}}]})
        tool_result = json.loads(body["messages"][-1]["content"])
        assert [item["kp_id"] for item in tool_result["data"]] == ["K-public"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "Only verified content."}}]})

    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: _provider(handler))
    token = _token(client, "student", dataset_id, "S01")
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "task_type": "knowledge_lookup", "messages": [{"role": "user", "content": "Find knowledge"}]
    })
    assert response.status_code == 200, response.text

    store._atomic_json(store._library(dataset_id, catalog.version) / "current.json", {"kb_version": "kb-mixed"})
    denied = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "task_type": "knowledge_lookup", "messages": [{"role": "user", "content": "Find knowledge"}]
    })
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "KB_NOT_PUBLISHED"


def test_chat_rejects_catalog_from_another_dataset(client: TestClient, seeded: dict, session, monkeypatch) -> None:
    other = models.Dataset(id="other-dataset", created_at="2026-01-02T00:00:00+00:00", parent_id=None,
                           status="ready", manifest_json={})
    session.add(other)
    session.commit()
    _catalog(session, "other-dataset", "other-cat")
    session.add(models.AnalysisRun(
        id="other-run", dataset_id="other-dataset", catalog_version="other-cat",
        rule_version="rules-v1", config_json={}, status="succeeded",
        created_at="2026-01-02T00:00:00+00:00",
    ))
    session.commit()
    token = _token(client, "teacher", seeded["dataset_id"])
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"task_type":"knowledge_lookup"}'}}]})

    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: _provider(handler))
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "task_type": "knowledge_lookup", "run_id": "other-run",
        "messages": [{"role": "user", "content": "Find knowledge"}]
    })
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CONTEXT_MISMATCH"
    assert calls == 0

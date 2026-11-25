from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider
from study_companion.db import models


def _session(client: TestClient, role: str, dataset_id: str, student_id: str | None = None) -> str:
    payload = {"role": role, "dataset_id": dataset_id}
    if student_id:
        payload["student_id"] = student_id
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _provider(handler):
    provider = OpenAICompatibleProvider("http://router.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://router.test/v1")
    return provider


def _add_second_class(session: Session, dataset_id: str) -> None:
    session.add(models.Student(dataset_id=dataset_id, student_id="S05", class_id="C02", grade="七年级"))
    result = session.get(models.AnalysisResult, ("synthetic-run", "class", "C01", "N01"))
    assert result is not None
    from copy import deepcopy
    payload = deepcopy(result.payload_json)
    payload["class_id"] = "C02"
    payload["observed_student_count"] = 1
    session.add(models.AnalysisResult(
        run_id="synthetic-run", subject_type="class", subject_id="C02", node_id="N01", payload_json=payload,
    ))
    session.commit()


def test_explicit_task_and_target_skips_classifier(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["tools"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "已读取本人学情。"}}]})

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01",
        "target": {"type": "student", "id": "S01"},
        "task_type": "student_loss_explanation",
        "messages": [{"role": "user", "content": "帮我看看"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task_type"] == "student_loss_explanation"
    assert data["route_source"] == "explicit"
    assert calls == 1


def test_ambiguous_request_classifies_without_tools_then_runs_task(client: TestClient, seeded: dict, monkeypatch) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert "tools" not in body
            assert body["response_format"] == {"type": "json_object"}
            assert json.loads(body["messages"][-1]["content"]) == {"request": "帮我看看"}
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"task_type":"student_loss_explanation"}'}}]})
        assert body["tools"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "观察到的情况有限。"}}]})

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01", "target": {"type": "student", "id": "S01"},
        "messages": [{"role": "user", "content": "帮我看看"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task_type"] == "student_loss_explanation"
    assert data["route_source"] == "model"
    assert data["prompt_version"].startswith("router-v1+task-student-loss-v1+")
    assert len(requests) == 2


def test_invalid_classifier_result_returns_task_unclear_422(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert "tools" not in json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "messages": [{"role": "user", "content": "帮我看看"}],
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TASK_UNCLEAR"
    assert calls == 1


def test_pending_task_returns_explicit_unavailable_error(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "unexpected"}}]})

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01", "target": {"type": "student", "id": "S01"},
        "task_type": "student_learning_advice",
        "messages": [{"role": "user", "content": "给学习建议"}],
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TASK_NOT_AVAILABLE"
    assert calls == 0


def test_student_cannot_select_another_student_or_class_target(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "student", seeded["dataset_id"], "S01")
    headers = {"Authorization": f"Bearer {token}"}
    base = {"run_id": "synthetic-run", "node_id": "N01", "messages": [{"role": "user", "content": "分析"}]}

    other = client.post("/api/v1/chat", headers=headers, json={**base, "target": {"type": "student", "id": "S02"}})
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "FORBIDDEN"

    class_target = client.post("/api/v1/chat", headers=headers, json={
        **base, "target": {"type": "class", "id": "C01"}, "task_type": "class_analysis",
    })
    assert class_target.status_code == 403
    assert class_target.json()["error"]["code"] == "FORBIDDEN"
    assert calls == 0


def test_student_mixed_teacher_intent_is_rejected_before_classifier(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "student", seeded["dataset_id"], "S01")
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01",
        "messages": [{"role": "user", "content": "请分析我的学习表现，再分析班级共性"}],
    })
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert calls == 0


def test_conflicting_legacy_and_explicit_targets_are_rejected(client: TestClient, seeded: dict, monkeypatch) -> None:
    provider = _provider(lambda _request: httpx.Response(500))
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01", "student_id": "S01",
        "target": {"type": "student", "id": "S02"},
        "messages": [{"role": "user", "content": "为什么失分"}],
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_class_analysis_is_bound_to_explicit_class_and_node(client: TestClient, seeded: dict, session: Session, monkeypatch) -> None:
    _add_second_class(session, seeded["dataset_id"])
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            tool = body["tools"][0]["function"]
            assert tool["name"] == "get_class_analysis"
            assert tool["parameters"]["required"] == []
            return httpx.Response(200, json={"choices": [{"message": {
                "tool_calls": [{"id": "class-call", "type": "function", "function": {
                    "name": "get_class_analysis", "arguments": '{"class_id":"C02","node_id":"N01"}'
                }}]
            }}]})
        tool_result = json.loads(body["messages"][-1]["content"])
        assert tool_result["data"]["result"]["class_id"] == "C01"
        return httpx.Response(200, json={"choices": [{"message": {"content": "当前班级结果已读取。"}}]})

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "run_id": "synthetic-run", "node_id": "N01", "target": {"type": "class", "id": "C01"},
        "task_type": "class_analysis", "messages": [{"role": "user", "content": "班级分析"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["task_type"] == "class_analysis"
    assert len(requests) == 2


def test_out_of_scope_task_returns_fixed_response_without_model_or_tools(client: TestClient, seeded: dict, monkeypatch) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = _provider(handler)
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _session(client, "teacher", seeded["dataset_id"])
    response = client.post("/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "task_type": "out_of_scope", "messages": [{"role": "user", "content": "讲个笑话"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["stopped_reason"] == "out_of_scope"
    assert data["tool_trace"] == []
    assert data["evidence_refs"] == []
    assert calls == 0

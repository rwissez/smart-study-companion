from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider


def _teacher_token(client: TestClient, dataset_id: str) -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "teacher", "dataset_id": dataset_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _student_token(client: TestClient, dataset_id: str, student_id: str) -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": dataset_id, "student_id": student_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def test_teacher_chat_replans_from_analysis_to_answer_evidence(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    requests: list[dict] = []

    def model_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]

        if len(requests) == 1:
            assert body["tools"]
            return httpx.Response(200, json={"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "analysis-1",
                    "type": "function",
                    "function": {
                        "name": "get_student_analysis",
                        "arguments": json.dumps({"student_id": "S01", "node_id": "N01"}),
                    },
                }],
            }}]})

        if len(requests) == 2:
            assistant, tool = messages[-2:]
            assert assistant["tool_calls"][0]["type"] == "function"
            assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
                "student_id": "S01", "node_id": "N01"
            }
            analysis = json.loads(tool["content"])
            assert analysis["status"] == "succeeded"
            assert analysis["data"]["result"]["student_id"] == "S01"
            refs = analysis["data"]["result"]["findings"][1]["evidence_refs"]
            assert any(ref["id"] == "R01" for ref in refs)
            return httpx.Response(200, json={"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "evidence-1",
                    "type": "function",
                    "function": {
                        "name": "get_answer_evidence",
                        "arguments": json.dumps({"record_ids": ["R01"]}),
                    },
                }],
            }}]})

        assert len(requests) == 3
        assistant, tool = messages[-2:]
        assert assistant["tool_calls"][0]["function"]["name"] == "get_answer_evidence"
        evidence = json.loads(tool["content"])
        assert evidence["status"] == "succeeded"
        assert evidence["data"][0]["record_id"] == "R01"
        assert evidence["data"][0]["payload"]["学号"] == "S01"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "该生首次作答失分，修订后得分改善；依据记录 R01 与指标 M_S01。"
        }}]})

    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(
        transport=httpx.MockTransport(model_handler),
        base_url="http://model.test/v1",
    )
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)

    token = _teacher_token(client, seeded["dataset_id"])
    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": "synthetic-run",
            "student_id": "S01",
            "node_id": "N01",
            "messages": [{"role": "user", "content": "S01 为什么失分？"}],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["stopped_reason"] == "model_finished"
    assert data["prompt_version"] == "task-student-loss-v1+teacher-style-v1"
    assert data["task_type"] == "student_loss_explanation"
    assert data["route_source"] == "rule"
    assert "R01" in data["text"] and "M_S01" in data["text"]
    assert [item["tool_name"] for item in data["tool_trace"]] == [
        "get_student_analysis", "get_answer_evidence"
    ]
    assert any(ref["id"] == "M_S01" for ref in data["evidence_refs"])
    assert any(ref["id"] == "R01" for ref in data["evidence_refs"])
    assert len(requests) == 3


def test_student_quality_query_is_bound_to_own_persisted_issues(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    calls = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            return httpx.Response(200, json={"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "quality-1",
                    "type": "function",
                    "function": {"name": "get_quality_issues", "arguments": "{}"},
                }],
            }}]})
        quality = json.loads(body["messages"][-1]["content"])
        issue_ids = {item["issue_id"] for item in quality["data"]}
        assert issue_ids == {"QI01", "QI03"}
        assert "QI02" not in issue_ids
        return httpx.Response(200, json={"choices": [{"message": {"content": "已读取本人数据质量问题。"}}]})

    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(
        transport=httpx.MockTransport(model_handler),
        base_url="http://model.test/v1",
    )
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)

    token = _student_token(client, seeded["dataset_id"], "S01")
    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": "synthetic-run",
            "student_id": "S01",
            "node_id": "N01",
            "messages": [{"role": "user", "content": "我的数据有哪些质量问题？"}],
        },
    )

    assert response.status_code == 200, response.text
    trace = response.json()["data"]["tool_trace"]
    assert {item["issue_id"] for item in trace[0]["result"]} == {"QI01", "QI03"}
    assert calls == 2

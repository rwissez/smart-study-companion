"""Agent 工具轨迹落库（05 §11.2、06 §4.5）。"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider
from study_companion.db import models


def _session(client: TestClient, **payload) -> str:
    return client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]


def test_tool_calls_are_persisted_without_raw_arguments(
    client: TestClient, seeded: dict, session: Session, monkeypatch
) -> None:
    requests: list[dict] = []

    def model_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "trace-call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_student_analysis",
                                            "arguments": json.dumps(
                                                {"student_id": "S01", "node_id": "N01"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "已读取学情并给出说明。"}}]},
        )

    provider = OpenAICompatibleProvider("http://model.test/v1", "m", "k")
    provider._client = httpx.Client(
        transport=httpx.MockTransport(model_handler), base_url="http://model.test/v1"
    )
    monkeypatch.setattr(
        "study_companion.api.routes.chat.build_provider", lambda _settings: provider
    )
    token = _session(
        client, role="teacher", dataset_id=seeded["dataset_id"]
    )
    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": seeded["run_id"],
            "node_id": "N01",
            "target": {"type": "student", "id": "S01"},
            "task_type": "student_loss_explanation",
            "messages": [{"role": "user", "content": "帮我看看这个学生"}],
        },
    )
    assert response.status_code == 200, response.text
    fact = response.json()["data"].get("fact_check")
    assert fact is not None and fact["status"] in {"passed", "warning"}

    from study_companion.db.session import get_sessionmaker

    with get_sessionmaker()() as fresh:
        traces = fresh.scalars(
            select(models.ToolTrace).where(models.ToolTrace.call_id == "trace-call-1")
        ).all()
    assert traces, "工具调用必须落库"
    trace = traces[0]
    assert trace.tool_name == "get_student_analysis"
    assert trace.status == "succeeded"
    assert trace.args_digest and trace.scope_digest
    assert trace.step_key.startswith("chat:")
    assert trace.result_refs_json is not None

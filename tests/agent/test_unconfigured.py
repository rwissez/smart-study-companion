from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from study_companion.agent import ModelTurn, build_provider
from study_companion.agent.providers.unconfigured import UnconfiguredProvider
from study_companion.config import Settings
from study_companion.contracts import DomainError


def test_unconfigured_provider_refuses_rather_than_inventing() -> None:
    provider = UnconfiguredProvider()

    with pytest.raises(DomainError) as excinfo:
        provider.generate([{"role": "user", "content": "你好"}])

    assert excinfo.value.code == "MODEL_NOT_CONFIGURED"


def test_factory_returns_unconfigured_when_env_is_blank() -> None:
    settings = Settings(model_provider="", model_base_url="", model_name="")
    assert settings.model_configured is False
    assert build_provider(settings).name == "unconfigured"


def test_model_turn_reports_tool_intent() -> None:
    assert ModelTurn(text="hi").wants_tools is False


def test_chat_route_returns_503_not_a_fake_answer(client: TestClient, seeded: dict) -> None:
    created = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": seeded["dataset_id"], "student_id": "S01"},
    )
    token = created.json()["data"]["session_token"]

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "这题怎么做"}]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "MODEL_NOT_CONFIGURED"
    assert "data" not in body


def test_bound_teacher_chat_also_reports_the_missing_model(
    client: TestClient, seeded: dict
) -> None:
    # Doc 09 and the /teacher/chat route both require teacher-side chat, so a
    # bound teacher must pass the guard and fail only on the absent model.
    created = client.post(
        "/api/v1/demo-sessions", json={"role": "teacher", "dataset_id": seeded["dataset_id"]}
    )
    token = created.json()["data"]["session_token"]

    response = client.post(
        "/api/v1/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "测试"}]},
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"

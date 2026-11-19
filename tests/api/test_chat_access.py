"""Both sides may chat; neither may do so outside its own scope.

The model is unconfigured in tests, so an authorised caller gets 503
MODEL_NOT_CONFIGURED. That is the success signal here: it proves the request
passed the capability guard and reached the provider. A 403 would mean the
guard rejected a role that is supposed to be allowed through.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

BODY = {"messages": [{"role": "user", "content": "这道题我哪一步错了？"}]}


def _token(client: TestClient, **payload) -> str:
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("role", ["teacher", "student"])
def test_both_roles_pass_the_guard_and_reach_the_provider(
    client: TestClient, seeded: dict, role: str
) -> None:
    extra = {"student_id": "S01"} if role == "student" else {}
    token = _token(client, role=role, dataset_id=seeded["dataset_id"], **extra)

    response = client.post("/api/v1/chat", json=BODY, headers=_headers(token))

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"


def test_bootstrap_teacher_cannot_chat_before_selecting_a_dataset(client: TestClient) -> None:
    token = _token(client, role="teacher")

    response = client.post("/api/v1/chat", json=BODY, headers=_headers(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_chat_requires_a_session_token(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json=BODY)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

"""会话归属的稳定身份键（01 §9.4）。

换标签页会拿到新的 demo session，所以会话归属改由服务端按“角色 + 数据集 +
本人”派生的 owner key 决定；这里验证同一 owner 可见、不同 owner 不可见、
身份切换不串人，以及旧会话仍能按原归属读取。
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from study_companion.agent.types import ModelTurn, ToolCall
from study_companion.api.routes.chat import derive_owner_key
from study_companion.db import models
from study_companion.jobs.service import now_iso


class _HistoryProvider:
    """先调用一次真实工具，再返回文本；用于真正走完一轮对话并落库。"""

    name = "fake_history"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "call-1",
                        "get_student_analysis",
                        {"student_id": "S01", "node_id": "N01"},
                    )
                ]
            )
        return ModelTurn(text="依据正式学情，先订正括号前的符号。")


def _new_session(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _headers(data: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {data['session_token']}"}


def _create_conversation(client: TestClient, headers: dict, seeded: dict) -> str:
    response = client.post(
        "/api/v1/chat",
        headers=headers,
        json={
            "message": "S01 为什么失分？",
            "run_id": seeded["run_id"],
            "student_id": "S01",
            "node_id": "N01",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["conversation_id"]


def _seed_provider(monkeypatch) -> _HistoryProvider:
    provider = _HistoryProvider()
    monkeypatch.setattr(
        "study_companion.api.routes.chat.build_provider", lambda _settings: provider
    )
    return provider


def _conversation_ids(client: TestClient, headers: dict) -> list[str]:
    response = client.get("/api/v1/conversations", headers=headers)
    assert response.status_code == 200, response.text
    return [item["conversation_id"] for item in response.json()["data"]["items"]]


def test_owner_key_separates_role_dataset_and_student() -> None:
    def key(**overrides) -> str:
        values = {
            "session_id": "sess-1",
            "role": "teacher",
            "dataset_id": "ds-1",
            "student_id": None,
        }
        values.update(overrides)
        return derive_owner_key(SimpleNamespace(**values))

    assert key() == key(session_id="sess-2"), "同一身份换 session 仍然是同一个 owner"
    assert key() != key(role="student", student_id="S01")
    assert key() != key(dataset_id="ds-2")
    assert key(role="student", student_id="S01") != key(role="student", student_id="S02")


def test_same_owner_in_a_new_tab_sees_and_continues_history(
    client: TestClient, seeded: dict, session: Session, monkeypatch
) -> None:
    _seed_provider(monkeypatch)
    first = _new_session(client, role="teacher", dataset_id=seeded["dataset_id"])
    conversation_id = _create_conversation(client, _headers(first), seeded)

    # 归属写的是稳定 owner key；owner_ref 仍保留创建它的 demo session。
    row = session.get(models.Conversation, conversation_id)
    assert row is not None
    session.refresh(row)
    assert row.scope_key.startswith("ok1_")
    assert row.owner_ref == first["scope"]["session_id"]

    # 新标签页：同一身份的新 demo session，没有共享 sessionStorage。
    second = _new_session(client, role="teacher", dataset_id=seeded["dataset_id"])
    assert second["scope"]["session_id"] != first["scope"]["session_id"]
    history = client.get("/api/v1/conversations", headers=_headers(second))
    assert history.status_code == 200, history.text
    data = history.json()["data"]
    assert data["owner_key"] == row.scope_key
    assert [item["conversation_id"] for item in data["items"]] == [conversation_id]

    # 带服务端回传的 owner key 再请求，仍然通过（只接受与会话身份一致的键）。
    with_owner_key = {**_headers(second), "X-Owner-Key": data["owner_key"]}
    assert _conversation_ids(client, with_owner_key) == [conversation_id]

    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(second)
    )
    assert messages.status_code == 200, messages.text
    assert [item["role"] for item in messages.json()["data"]["items"]] == [
        "user",
        "assistant",
    ]

    # 新标签页继续追问：消息追加到同一个会话，而不是另起一份。
    continued = client.post(
        "/api/v1/chat",
        headers=_headers(second),
        json={
            "message": "再说说哪里失分",
            "conversation_id": conversation_id,
            "run_id": seeded["run_id"],
            "student_id": "S01",
            "node_id": "N01",
        },
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["data"]["conversation_id"] == conversation_id
    after = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(second)
    )
    assert after.status_code == 200
    assert [item["role"] for item in after.json()["data"]["items"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_student_same_owner_in_a_new_tab_sees_history(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    _seed_provider(monkeypatch)
    dataset_id = seeded["dataset_id"]
    first = _new_session(client, role="student", dataset_id=dataset_id, student_id="S01")
    conversation_id = _create_conversation(client, _headers(first), seeded)

    second = _new_session(client, role="student", dataset_id=dataset_id, student_id="S01")
    assert second["scope"]["session_id"] != first["scope"]["session_id"]
    assert _conversation_ids(client, _headers(second)) == [conversation_id]

    # 同一数据集里的另一名学生不是同一个 owner。
    other = _new_session(client, role="student", dataset_id=dataset_id, student_id="S02")
    assert _conversation_ids(client, _headers(other)) == []
    hidden = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(other)
    )
    assert hidden.status_code == 404


def test_other_owner_cannot_see_or_forge_history(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    _seed_provider(monkeypatch)
    teacher = _new_session(client, role="teacher", dataset_id=seeded["dataset_id"])
    conversation_id = _create_conversation(client, _headers(teacher), seeded)
    owner_key = client.get(
        "/api/v1/conversations", headers=_headers(teacher)
    ).json()["data"]["owner_key"]

    # 同一数据集里的另一个学生是另一个 owner。
    student = _new_session(
        client, role="student", dataset_id=seeded["dataset_id"], student_id="S01"
    )
    assert _conversation_ids(client, _headers(student)) == []
    hidden = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_headers(student)
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "NOT_FOUND"

    # 客户端不能拿别人的 owner key 换取别人的历史：服务端按会话身份重算并拒绝。
    forged = client.get(
        "/api/v1/conversations",
        headers={**_headers(student), "X-Owner-Key": owner_key},
    )
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "FORBIDDEN"

    # 同角色不同本人也互不可见。
    other_student = _new_session(
        client, role="student", dataset_id=seeded["dataset_id"], student_id="S02"
    )
    assert _conversation_ids(client, _headers(other_student)) == []


def test_switching_role_does_not_leak_history(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    _seed_provider(monkeypatch)
    teacher = _new_session(client, role="teacher", dataset_id=seeded["dataset_id"])
    conversation_id = _create_conversation(client, _headers(teacher), seeded)

    student = _new_session(
        client, role="student", dataset_id=seeded["dataset_id"], student_id="S01"
    )
    assert _conversation_ids(client, _headers(student)) == []

    # 切回同一角色（新会话）仍能看到自己的历史，证明隔离不是把历史弄丢。
    back_to_teacher = _new_session(
        client, role="teacher", dataset_id=seeded["dataset_id"]
    )
    assert _conversation_ids(client, _headers(back_to_teacher)) == [conversation_id]


def test_legacy_session_owned_conversation_stays_readable(
    client: TestClient, seeded: dict, session: Session, monkeypatch
) -> None:
    dataset_id = seeded["dataset_id"]
    original = _new_session(client, role="teacher", dataset_id=dataset_id)
    created_at = now_iso()
    legacy_id = "conv-legacy-owner-1"
    session.add(
        models.Conversation(
            conversation_id=legacy_id,
            owner_ref=original["scope"]["session_id"],
            # 旧版本的归属键只有角色、数据集与本人，没有 owner key。
            scope_key=f"teacher:{dataset_id}:",
            dataset_id=dataset_id,
            role="teacher",
            title="换标签页之前的历史",
            context_json={},
            context_revision=0,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.flush()
    session.add(
        models.ChatMessageRow(
            message_id="msg-legacy-owner-1",
            conversation_id=legacy_id,
            sequence=1,
            role="user",
            content="这是旧会话里的问题",
            status="completed",
            payload_json={},
            created_at=created_at,
        )
    )
    session.commit()

    # 原标签页按 demo session 兼容读取。
    assert _conversation_ids(client, _headers(original)) == [legacy_id]
    original_messages = client.get(
        f"/api/v1/conversations/{legacy_id}/messages", headers=_headers(original)
    )
    assert original_messages.status_code == 200

    # 新标签页按同身份的旧归属键兼容读取。
    new_tab = _new_session(client, role="teacher", dataset_id=dataset_id)
    assert _conversation_ids(client, _headers(new_tab)) == [legacy_id]

    # 旧会话也能从新标签页继续追问，而不是被当成别人的会话拒绝。
    _seed_provider(monkeypatch)
    continued = client.post(
        "/api/v1/chat",
        headers=_headers(new_tab),
        json={
            "message": "S01 为什么失分？",
            "conversation_id": legacy_id,
            "run_id": seeded["run_id"],
            "student_id": "S01",
            "node_id": "N01",
        },
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["data"]["conversation_id"] == legacy_id
    continued_messages = client.get(
        f"/api/v1/conversations/{legacy_id}/messages", headers=_headers(new_tab)
    )
    assert [item["role"] for item in continued_messages.json()["data"]["items"]] == [
        "user",
        "user",
        "assistant",
    ]

    # 其他身份仍然看不到这条旧会话。
    student = _new_session(
        client, role="student", dataset_id=dataset_id, student_id="S01"
    )
    assert _conversation_ids(client, _headers(student)) == []
    hidden = client.get(
        f"/api/v1/conversations/{legacy_id}/messages", headers=_headers(student)
    )
    assert hidden.status_code == 404

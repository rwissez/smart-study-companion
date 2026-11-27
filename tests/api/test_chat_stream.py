from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from study_companion.agent.types import ModelTurn, ToolCall


def _teacher_token(client: TestClient, dataset_id: str) -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "teacher", "dataset_id": dataset_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


class ChunkedProvider:
    """没有原生流接口的 provider：走“按最终文本分块”的兼容路径。"""

    name = "fake_chunked"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            names = [tool["function"]["name"] for tool in tools or []]
            assert "get_student_analysis" in names
            assert "get_answer_evidence" in names
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "call-1",
                        "get_student_analysis",
                        {"student_id": "S01", "node_id": "N01"},
                    )
                ]
            )
        return ModelTurn(text="## 结论\n\n- 依据 **R01**，先订正符号。\n\n---\n\n下一步：重做同类题。")


class NativeProvider(ChunkedProvider):
    """模拟支持原生流的 provider，逐字回调。"""

    name = "fake_native"

    def __init__(self) -> None:
        super().__init__()
        self.streamed = False

    def generate_stream(self, messages, tools=None, on_text=None, response_schema=None) -> ModelTurn:
        self.streamed = True
        text = "先看括号前的符号，再逐项检查。"
        for char in text:
            if callable(on_text):
                on_text(char)
        return ModelTurn(text=text)


def _frames(response) -> list[tuple[str, str]]:
    frames: list[tuple[str, str]] = []
    event = "message"
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                frames.append((event, "\n".join(data)))
            event, data = "message", []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        frames.append((event, "\n".join(data)))
    return frames


def test_stream_reports_real_tool_steps_and_persists_history(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    provider = ChunkedProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": "synthetic-run",
            "student_id": "S01",
            "node_id": "N01",
            "message": "S01 为什么失分？",
        },
    ) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = _frames(response)

    assert provider.calls == 2
    kinds = [item[0] for item in frames]
    assert "step" in kinds and "delta" in kinds and "done" in kinds
    assert "discard" not in kinds

    import json

    steps = [json.loads(data) for event, data in frames if event == "step"]
    labels = [step["label"] for step in steps]
    assert any("正在调用：读取绑定目标学生和节点的分析结果" in label for label in labels), labels
    assert any("已完成：读取绑定目标学生和节点的分析结果" in label for label in labels), labels
    assert any(step["status"] == "running" for step in steps)
    assert any(step["status"] == "succeeded" for step in steps)

    deltas = [json.loads(data) for event, data in frames if event == "delta"]
    assert deltas and all(item["mode"] == "chunked" for item in deltas)
    assert "".join(item["text"] for item in deltas).startswith("## 结论")

    done = json.loads([data for event, data in frames if event == "done"][0])
    assert done["stream_mode"] == "chunked"
    assert done["conversation_id"].startswith("conv-")
    assert "R01" in done["text"]

    history = client.get("/api/v1/conversations", headers={"Authorization": f"Bearer {token}"})
    assert history.status_code == 200
    items = history.json()["data"]["items"]
    assert [item["conversation_id"] for item in items] == [done["conversation_id"]]

    messages = client.get(
        f"/api/v1/conversations/{done['conversation_id']}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert messages.status_code == 200
    rows = messages.json()["data"]["items"]
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[1]["message_id"] == done["message_id"]


def test_stream_uses_native_provider_deltas(client: TestClient, seeded: dict, monkeypatch) -> None:
    provider = NativeProvider()
    monkeypatch.setattr("study_companion.api.routes.chat.build_provider", lambda _settings: provider)
    token = _teacher_token(client, seeded["dataset_id"])

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"run_id": "synthetic-run", "student_id": "S01", "node_id": "N01", "message": "S01 为什么失分？"},
    ) as response:
        assert response.status_code == 200, response.read()
        frames = _frames(response)

    import json

    deltas = [json.loads(data) for event, data in frames if event == "delta"]
    assert provider.streamed is True
    assert deltas and all(item["mode"] == "native" for item in deltas)
    done = json.loads([data for event, data in frames if event == "done"][0])
    assert done["stream_mode"] == "native"
    assert done["native_stream"] is True


def test_post_chat_commits_conversation_before_response(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    provider = ChunkedProvider()
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
    conversation_id = response.json()["data"]["conversation_id"]

    # 紧接着另起请求读取历史：会话必须在响应前已提交，不能等依赖清理再落库。
    history = client.get("/api/v1/conversations", headers={"Authorization": f"Bearer {token}"})
    assert history.status_code == 200
    assert conversation_id in [item["conversation_id"] for item in history.json()["data"]["items"]]

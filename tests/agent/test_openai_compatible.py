from __future__ import annotations

import httpx
import pytest
import json

from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider
from study_companion.contracts import DomainError


def test_openai_compatible_parses_tool_calls(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read()
        assert b'"tools"' in body
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "call-1", "function": {"name": "get_student_analysis", "arguments": json.dumps({"student_id": "S01", "node_id": "N01"})}}]
        }}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}})
    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://model.test/v1")
    turn = provider.generate([{"role": "user", "content": "why"}], tools=[{"type": "function", "function": {"name": "get_student_analysis"}}])
    assert turn.tool_calls[0].name == "get_student_analysis"
    assert turn.tool_calls[0].arguments["student_id"] == "S01"
    assert turn.usage.prompt_tokens == 3


def test_openai_compatible_maps_timeout(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("late")
    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://model.test/v1")
    with pytest.raises(DomainError) as exc:
        provider.generate([])
    assert exc.value.code == "MODEL_TIMEOUT"


def test_openai_compatible_streams_text_deltas():
    stream = (
        'data: {"choices":[{"delta":{"content":"先看"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"符号"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(200, content=stream.encode("utf-8"))

    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://model.test/v1")
    seen: list[str] = []
    turn = provider.generate_stream(
        [{"role": "user", "content": "解释"}],
        on_text=seen.append,
    )
    assert seen == ["先看", "符号"]
    assert turn.text == "先看符号"
    assert turn.tool_calls == []


def test_openai_compatible_stream_accumulates_tool_call_fragments():
    stream = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
        '"function":{"name":"get_student_analysis","arguments":"{\\"student_id\\":"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"\\"S01\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream.encode("utf-8"))

    provider = OpenAICompatibleProvider("http://model.test/v1", "demo")
    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://model.test/v1")
    turn = provider.generate_stream([{"role": "user", "content": "分析"}])
    assert [call.name for call in turn.tool_calls] == ["get_student_analysis"]
    assert turn.tool_calls[0].arguments == {"student_id": "S01"}
    assert turn.text is None

"""回答事实绑定（05 §10）：数字必须有本轮出处，证据编号必须在本轮授权集合内。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from study_companion.agent import AgentLoop, RegisteredTool, ToolRegistry
from study_companion.agent.answer_guard import bind_answer
from study_companion.agent.loop import GUARD_FALLBACK_TEXT
from study_companion.agent.types import ModelTurn, ToolCall

SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_metric",
        "description": "读取一条指标",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

METRIC_RESULT = {
    "status": "succeeded",
    "data": {"rate": 0.8, "numerator": 4, "denominator": 5},
    "evidence_refs": [{"kind": "metric", "id": "m1"}],
}


def _registry(result: dict | None = None) -> ToolRegistry:
    payload = result or METRIC_RESULT
    return ToolRegistry([RegisteredTool(SCHEMA, lambda _args: payload)])


@dataclass
class ScriptedProvider:
    turns: list[ModelTurn] = field(default_factory=list)
    calls: int = 0

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        return self.turns.pop(0)


def test_unbound_number_is_removed_and_turned_into_limitation() -> None:
    binding = bind_answer(
        text="本次得分率是 0.83。",
        tool_results=[{"rate": 0.8}],
    )

    assert binding.status == "sanitized"
    assert binding.changed is True
    assert "0.83" not in binding.text
    assert "0.83" in binding.removed_numbers
    assert "限制" in binding.text and "没有在本轮工具结果中找到出处" in binding.text
    assert binding.draft and binding.draft[-1].kind == "limitation"


def test_evidence_number_is_kept() -> None:
    binding = bind_answer(
        text="本次得分率是 0.8，4/5 道题做对。",
        tool_results=[{"rate": 0.8, "numerator": 4, "denominator": 5}],
    )

    assert binding.status == "passed"
    assert binding.changed is False
    assert binding.text == "本次得分率是 0.8，4/5 道题做对。"
    assert binding.removed_numbers == ()


def test_percent_format_is_checked_by_deterministic_rule() -> None:
    binding = bind_answer(text="得分率 80%。", tool_results=[{"rate": 0.8}])

    assert binding.status == "passed"
    assert binding.text == "得分率 80%。"


def test_unknown_evidence_sentence_is_removed() -> None:
    binding = bind_answer(
        text="依据 R99 可以看出学生已经掌握。下一步继续练习。",
        tool_results=[],
        allowed_evidence_ids={"R01"},
    )

    assert "R99" not in binding.text
    assert "R99" in binding.removed_evidence
    assert "下一步继续练习" in binding.text
    assert "不在本轮授权结果中" in binding.text


def test_authorized_evidence_sentence_is_kept() -> None:
    binding = bind_answer(
        text="依据 R01 可以看出学生已经掌握这一题型。",
        tool_results=[],
        allowed_evidence_ids={"R01"},
    )

    assert binding.status == "passed"
    assert "R01" in binding.text


def test_data_claim_without_any_evidence_is_removed() -> None:
    binding = bind_answer(text="数据显示这个班整体掌握较好。", tool_results=[])

    assert "数据显示" not in binding.text
    assert "数据结论已删去" in binding.text


def test_reference_numbers_do_not_need_binding() -> None:
    binding = bind_answer(text="先看第 2 步，再做第 3 页的练习。", tool_results=[])

    assert binding.status == "passed"
    assert binding.text == "先看第 2 步，再做第 3 页的练习。"


def test_loop_blocks_unbound_number_before_returning_final_text() -> None:
    provider = ScriptedProvider([
        ModelTurn(tool_calls=[ToolCall("c1", "read_metric", {})]),
        ModelTurn(text="本轮得分率是 0.95。"),
    ])

    result = AgentLoop(provider, _registry()).run([{"role": "user", "content": "看看指标"}], "读取指标")

    assert result.stopped_reason == "model_finished"
    assert "0.95" not in (result.text or "")
    assert "限制" in (result.text or "")
    assert result.guard is not None
    assert result.guard["fact_binding"]["status"] == "sanitized"


class StreamingProvider:
    name = "fake_streaming"

    def __init__(self) -> None:
        self.calls = 0

    def generate_stream(self, messages, tools=None, on_text=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(tool_calls=[ToolCall("c1", "read_metric", {})])
        text = "本轮得分率是 0.95。"
        for char in text:
            if callable(on_text):
                on_text(char)
        return ModelTurn(text=text)


def test_streaming_preview_is_discarded_and_replaced_with_verified_text() -> None:
    provider = StreamingProvider()
    events: list[tuple[str, str]] = []

    def on_delta(chunk: str) -> None:
        events.append(("delta", chunk))

    def on_event(event: dict) -> None:
        events.append(("event", str(event.get("type"))))

    result = AgentLoop(provider, _registry()).run(
        [{"role": "user", "content": "看看指标"}],
        "读取指标",
        on_event=on_event,
        on_delta=on_delta,
    )

    assert "discard_delta" in [value for kind, value in events if kind == "event"]
    discard_index = events.index(("event", "discard_delta"))
    resent = "".join(value for kind, value in events[discard_index:] if kind == "delta")
    assert "0.95" not in resent
    assert resent == result.text


def test_loop_degrades_to_safe_text_when_binding_fails(monkeypatch) -> None:
    def boom(**_kwargs):
        raise RuntimeError("binding unavailable")

    monkeypatch.setattr("study_companion.agent.loop.bind_answer", boom)
    provider = ScriptedProvider([ModelTurn(text="本轮得分率是 0.95。")])

    result = AgentLoop(provider, ToolRegistry()).run([{"role": "user", "content": "看看"}], "回答")

    assert result.text == GUARD_FALLBACK_TEXT
    assert "0.95" not in result.text
    assert result.guard["fact_binding"]["status"] == "degraded"


def test_loop_without_numbers_is_unchanged() -> None:
    provider = ScriptedProvider([ModelTurn(text="先看括号前的符号，再逐项检查。")])

    result = AgentLoop(provider, ToolRegistry()).run([{"role": "user", "content": "怎么做"}], "回答")

    assert result.text == "先看括号前的符号，再逐项检查。"
    assert result.guard["fact_binding"]["status"] == "passed"

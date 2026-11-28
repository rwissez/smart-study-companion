"""学生求助答案保护（05 §8、04 §6.1）：泄题回复替换，安全回复保留，无法验证降级。"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.agent import AgentLoop, ToolRegistry
from study_companion.agent.tutoring_guard import (
    CHECK_FAILED_LIMITATION,
    SAFE_PRINCIPLE_REPLY,
    UNVERIFIED_LIMITATION,
    guard_tutoring_reply,
    is_tutoring_request,
)
from study_companion.agent.types import ModelTurn

PROTECTED_ANSWER = "去括号后得到 -2x+7"
LEAKING_REPLY = "直接算就好：去括号后得到 -2x+7，所以选这个。"
SAFE_REPLY = "先看括号前的符号，再逐项检查。"


def _student_messages(user_text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": "你是学习助手。\n学生端总则\n七年级学生能懂的话。",
        },
        {"role": "user", "content": user_text},
    ]


@dataclass
class ScriptedProvider:
    text: str
    calls: int = 0
    turns: list[ModelTurn] = field(default_factory=list)

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        return ModelTurn(text=self.text)


def test_help_patterns_cover_how_to_answer_and_hint_requests() -> None:
    assert is_tutoring_request("这道题第一步怎么做")
    assert is_tutoring_request("直接告诉我答案")
    assert is_tutoring_request("给点提示")
    assert is_tutoring_request("我卡住了")
    assert not is_tutoring_request("我今天该练什么")
    assert not is_tutoring_request("帮我看一下学习计划")


def test_leaking_reply_is_replaced_with_safe_principle() -> None:
    decision = guard_tutoring_reply(
        text=LEAKING_REPLY,
        request_text="这道题怎么做",
        protected_answers=[PROTECTED_ANSWER],
    )

    assert decision.action == "replaced"
    assert decision.text == SAFE_PRINCIPLE_REPLY
    assert "-2x+7" not in decision.text
    assert decision.verified is True
    assert decision.leaked_against == "参考答案"


def test_safe_reply_is_kept_when_protection_can_verify_it() -> None:
    decision = guard_tutoring_reply(
        text=SAFE_REPLY,
        request_text="这道题怎么做",
        protected_answers=[PROTECTED_ANSWER],
    )

    assert decision.action == "allow"
    assert decision.text == SAFE_REPLY
    assert decision.verified is True


def test_reply_is_replaced_when_protection_cannot_be_verified() -> None:
    decision = guard_tutoring_reply(
        text="先算括号里的 3+2，再乘 4。",
        request_text="这道题怎么做",
        protected_answers=[],
    )

    assert decision.action == "replaced"
    assert decision.text == SAFE_PRINCIPLE_REPLY
    assert decision.verified is False
    assert UNVERIFIED_LIMITATION in decision.limitations


def test_non_help_request_is_not_blocked() -> None:
    decision = guard_tutoring_reply(
        text="今天先练基础题。",
        request_text="我今天该练什么",
        protected_answers=[],
    )

    assert decision.action == "allow"
    assert decision.text == "今天先练基础题。"


def test_explicit_solution_permission_allows_reply() -> None:
    decision = guard_tutoring_reply(
        text=LEAKING_REPLY,
        request_text="这道题怎么做",
        protected_answers=[PROTECTED_ANSWER],
        can_show_solution=True,
    )

    assert decision.action == "allow"
    assert decision.text == LEAKING_REPLY


def test_check_failure_degrades_to_safe_principle(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("leak_check unavailable")

    monkeypatch.setattr("study_companion.agent.tutoring_guard.check_hint", boom)

    decision = guard_tutoring_reply(
        text=LEAKING_REPLY,
        request_text="这道题怎么做",
        protected_answers=[PROTECTED_ANSWER],
    )

    assert decision.action == "replaced"
    assert decision.text == SAFE_PRINCIPLE_REPLY
    assert decision.limitations == (CHECK_FAILED_LIMITATION,)


def test_loop_replaces_leaking_tutoring_reply_before_returning() -> None:
    provider = ScriptedProvider(text=LEAKING_REPLY)
    loop = AgentLoop(
        provider,
        ToolRegistry(),
        protected_answers=[PROTECTED_ANSWER],
    )

    result = loop.run(_student_messages("这道题我卡住了，第一步怎么做"), "引导学生")

    assert result.text == SAFE_PRINCIPLE_REPLY
    assert "-2x+7" not in (result.text or "")
    assert result.guard["tutoring"]["action"] == "replaced"


def test_loop_replaces_tutoring_reply_when_no_protected_set() -> None:
    provider = ScriptedProvider(text="先算括号里的 3+2，再乘 4。")
    loop = AgentLoop(provider, ToolRegistry())

    result = loop.run(_student_messages("直接告诉我答案"), "引导学生")

    assert result.text == SAFE_PRINCIPLE_REPLY
    assert "3+2" not in (result.text or "")
    assert result.guard["tutoring"]["verified"] is False


def test_loop_keeps_safe_tutoring_reply_after_protection_check() -> None:
    provider = ScriptedProvider(text=SAFE_REPLY)
    loop = AgentLoop(
        provider,
        ToolRegistry(),
        protected_answers=[PROTECTED_ANSWER],
    )

    result = loop.run(_student_messages("这道题怎么做"), "引导学生")

    assert result.text == SAFE_REPLY
    assert result.guard["tutoring"]["action"] == "allow"


def test_loop_keeps_student_non_help_answer() -> None:
    provider = ScriptedProvider(text="今天先练基础题，再练变式。")
    loop = AgentLoop(provider, ToolRegistry())

    result = loop.run(_student_messages("我今天该练什么"), "回答")

    assert result.text == "今天先练基础题，再练变式。"
    assert "tutoring" not in (result.guard or {})


def test_loop_degrades_when_tutoring_guard_raises(monkeypatch) -> None:
    def boom(**_kwargs):
        raise RuntimeError("guard unavailable")

    monkeypatch.setattr("study_companion.agent.loop.guard_tutoring_reply", boom)
    provider = ScriptedProvider(text=LEAKING_REPLY)

    result = AgentLoop(provider, ToolRegistry()).run(
        _student_messages("这道题怎么做"), "引导学生"
    )

    assert result.text == SAFE_PRINCIPLE_REPLY
    assert result.guard["tutoring"]["action"] == "degraded"

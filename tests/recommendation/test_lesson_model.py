"""C 微课堂模型协作：生成、复核与回退（04 §5）。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.contracts import EvidenceRef, LessonDraft
from study_companion.recommendation.lesson import validate_lesson
from study_companion.recommendation.lesson_model import (
    REQUIRED_REVIEW_CHECKS,
    LessonModelOut,
    LessonReviewCheckOut,
    LessonReviewOut,
    generate_with_model,
)


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


def _pass_review() -> LessonReviewOut:
    return LessonReviewOut(
        decision="pass",
        checks=[
            LessonReviewCheckOut(check_id=check_id, result="pass", reason="")
            for check_id in REQUIRED_REVIEW_CHECKS
        ],
    )


class FakeService:
    def __init__(self, write: LessonModelOut | None, review: LessonReviewOut) -> None:
        self.write = write
        self.review = review
        self.calls: list[str] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        if request.task_key.endswith("write_content.v1"):
            if self.write is None:
                return Result(False, None, {"code": "MODEL_TIMEOUT"})
            return Result(True, self.write)
        return Result(True, self.review)


def _draft() -> LessonModelOut:
    return LessonModelOut.model_validate(
        {
            "title": "去括号时的符号",
            "goal": "能把括号前的负号分配到每一项",
            "estimated_minutes": 4,
            "components": [
                {"id": "t", "type": "text", "heading": "先看符号", "paragraphs": ["括号前是负号时，每一项都要变号。"]},
                {
                    "id": "c",
                    "type": "contrast",
                    "before": "-(2x-7)",
                    "incorrect": "-2x-7",
                    "correct": "-2x+7",
                    "note": "负号要乘到每一项",
                },
            ],
            "fallback_text": "负号要乘到括号里的每一项。",
        }
    )


def _refs() -> list[EvidenceRef]:
    return [EvidenceRef(kind="knowledge_point", id="kp-1", knowledge_version="kn-1")]


def test_model_draft_is_used_when_review_passes() -> None:
    service = FakeService(_draft(), _pass_review())
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=["负号分配到括号内每一项", "上次去括号时漏乘了第二项，符号也写错了"],
        protected_texts=["受保护题 Q1 的原文"],
    )
    assert isinstance(draft, LessonDraft)
    assert draft.kp_ids == ["kp-1"]
    assert [item.type for item in draft.components] == ["text", "contrast", "citation"]
    assert limitations == []
    assert service.calls == ["c.write_content.v1", "c.review_lesson.v1"]


def test_review_rejection_falls_back_to_rule() -> None:
    service = FakeService(
        _draft(),
        LessonReviewOut(decision="reject", reasons=["对照里的正确式与原式不等价"]),
    )
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=["负号分配到每一项"],
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "复核未通过" in limitations[0]


def test_model_failure_returns_none_and_reason() -> None:
    service = FakeService(None, LessonReviewOut(decision="pass"))
    draft, limitations = generate_with_model(
        service=service,  # type: ignore[arg-type]
        title="去括号",
        goal="会变号",
        kp_id="kp-1",
        evidence_refs=_refs(),
        teaching_points=[],
        protected_texts=[],
    )
    assert draft is None
    assert limitations and "MODEL_TIMEOUT" in limitations[0]


def test_protected_material_in_model_draft_is_blocked_by_validator() -> None:
    draft = _draft()
    draft.components[0].paragraphs = ["受保护题目 Q1 的原文（这一句是题目内容）"]
    from study_companion.recommendation.lesson import build_rule_lesson

    lesson = LessonDraft(
        title=draft.title,
        goal=draft.goal,
        estimated_minutes=3,
        kp_ids=["kp-1"],
        evidence_refs=_refs(),
        components=draft.components,
        fallback_text=draft.fallback_text,
    )
    issues = validate_lesson(lesson, protected_texts=["受保护题目 Q1 的原文（这一句是题目内容）"])
    assert any(item.code == "ANSWER_PROTECTION" for item in issues)

"""C 微课堂模型链路的契约回归。

本轮真实模型实验暴露了两个问题，用合成输入固定下来：

1. 模型会把题目描述、中文说明或连等式写进 ``formula``/``derivation``/``contrast``
   的数学字段；这些字段只能放纯算式，必须在调用独立复核前被确定性硬检拒绝。
2. 八项复核必须逐项齐全；任意一项缺失都不能因为总体 ``decision=pass`` 而放行。

另外补两条来源契约：``citation`` 只能由程序按证据生成，``creation_method`` 默认
为 ``rule``，只有真正返回模型草稿时调用方才能标 ``model``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from study_companion.contracts import EvidenceRef, LessonDraft
from study_companion.recommendation.lesson import (
    build_rule_lesson,
    build_student_error_context,
    lesson_payload,
    review_info_for,
    validate_lesson,
)
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
    value: object = None
    error: dict | None = None


class ScriptedService:
    def __init__(self, *, write, review) -> None:  # noqa: ANN001
        self.write = write
        self.review = review
        self.calls: list[str] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        if request.task_key.endswith("write_content.v1"):
            if isinstance(self.write, Exception):
                raise self.write
            if self.write is None:
                return Result(False, None, {"code": "MODEL_TIMEOUT"})
            return Result(True, self.write)
        if isinstance(self.review, Exception):
            raise self.review
        return Result(True, self.review)


def _refs() -> list[EvidenceRef]:
    return [EvidenceRef(kind="knowledge_point", id="kp-T02I1", knowledge_version="kn-1")]


def _pass_checks(*, omit: str | None = None) -> list[LessonReviewCheckOut]:
    return [
        LessonReviewCheckOut(check_id=check_id, result="pass", reason="")
        for check_id in REQUIRED_REVIEW_CHECKS
        if check_id != omit
    ]


def _pass_review() -> LessonReviewOut:
    return LessonReviewOut(decision="pass", checks=_pass_checks())


def _draft(**overrides) -> LessonModelOut:  # noqa: ANN003
    payload = {
        "title": "移项变号：把 -6 搬到右边写成 +6",
        "goal": "能把常数项移项并正确变号",
        "estimated_minutes": 4,
        "components": [
            {
                "id": "t1",
                "type": "text",
                "heading": "先看这次卡在哪一步",
                "paragraphs": ["上次你在移项时把 -6 搬到右边没有变号，本课只练这一步。"],
            },
            {
                "id": "d1",
                "type": "derivation",
                "mode": "equation",
                "steps": [
                    {"expr": "3*x-6=15", "note": "原方程"},
                    {"expr": "3*x=15+6", "note": "两边同加 6，-6 移到右边变为 +6"},
                    {"expr": "x=7", "note": "两边同除以 3"},
                ],
            },
            {
                "id": "c1",
                "type": "contrast",
                "before": "3*x-6=15",
                "incorrect": "3*x=15-6",
                "correct": "3*x=15+6",
                "note": "移项要变号",
            },
        ],
        "fallback_text": "移项时把常数项移到另一边要变号。",
    }
    payload.update(overrides)
    return LessonModelOut.model_validate(payload)


ERROR_POINTS = [
    "等式性质解一元一次方程的知识点定义",
    "上次移项时把 -6 搬到右边写成 5-6，正确应为 5+6，解出 x=-1。",
]


def _generate(service: ScriptedService, **overrides):  # noqa: ANN001, ANN003
    kwargs = {
        "service": service,
        "title": "运用等式性质解一元一次方程",
        "goal": "会移项变号",
        "kp_id": "kp-T02I1",
        "evidence_refs": _refs(),
        "teaching_points": ERROR_POINTS,
        "protected_texts": [],
    }
    kwargs.update(overrides)
    return generate_with_model(**kwargs)  # type: ignore[arg-type]


def _first_math_component(draft: LessonModelOut, component_type: str):  # noqa: ANN001
    return next(item for item in draft.components if item.type == component_type)


def test_contrast_description_in_math_field_falls_back_before_review() -> None:
    draft_out = _draft()
    contrast = _first_math_component(draft_out, "contrast")
    contrast.before = "解方程 3*x+7=22"  # type: ignore[union-attr]
    service = ScriptedService(write=draft_out, review=_pass_review())

    draft, limitations = _generate(service)

    assert draft is None
    assert limitations and "数学表达式硬校验" in limitations[0]
    assert "c1.before" in limitations[0]
    # 数学硬检在独立复核之前失败，不能浪费一次复核调用。
    assert service.calls == ["c.write_content.v1"]


def test_chained_equation_in_derivation_step_falls_back() -> None:
    draft_out = _draft()
    derivation = _first_math_component(draft_out, "derivation")
    derivation.steps[1].expr = "3*x=15+6=21"  # type: ignore[union-attr]
    service = ScriptedService(write=draft_out, review=_pass_review())

    draft, limitations = _generate(service)

    assert draft is None
    assert limitations and "比较式最多一处关系" in limitations[0]
    assert service.calls == ["c.write_content.v1"]


def test_variable_outside_allowed_set_falls_back() -> None:
    draft_out = _draft()
    derivation = _first_math_component(draft_out, "derivation")
    derivation.steps[0].expr = "5*m-3"  # type: ignore[union-attr]
    service = ScriptedService(write=draft_out, review=_pass_review())

    draft, limitations = _generate(service)

    assert draft is None
    assert limitations and "数学表达式硬校验" in limitations[0]
    assert service.calls == ["c.write_content.v1"]


def test_prompt_declares_pure_math_fields_and_variable_set() -> None:
    prompt = (
        Path(__file__).resolve().parents[2] / "prompts" / "c" / "write_content.v1.md"
    ).read_text(encoding="utf-8")

    # 真实模型失败的主因是数学字段写进中文或连等式，prompt 必须显式约束。
    assert "contrast.before" in prompt
    assert "数学字段" in prompt
    assert "只用这四个字母" in prompt
    assert "最多一个关系符" in prompt
    assert "不要写 `10a`" in prompt
    assert "LaTeX" in prompt


def test_diagnostic_detail_with_correct_answer_marker_is_concrete_error() -> None:
    context = build_student_error_context(
        teaching_points=[
            "表示数量关系的知识点定义",
            "语义诊断：把女生人数写成 2(x-3)，多了括号；正确应为 2x-3。",
        ],
        evidence_refs=[],
        knowledge_definition="表示数量关系的知识点定义",
    )

    assert context["basis"] == "student_error"
    assert context["personalized"] is True
    assert "正确应为" in context["observed_error_notes"][0]


@pytest.mark.parametrize("missing", REQUIRED_REVIEW_CHECKS)
def test_every_missing_review_check_falls_back(missing: str) -> None:
    review = LessonReviewOut(decision="pass", checks=_pass_checks(omit=missing))
    service = ScriptedService(write=_draft(), review=review)

    draft, limitations = _generate(service)

    assert draft is None
    assert limitations and "不完整" in limitations[0]
    assert missing in limitations[0]
    # 缺项不是可定位问题，不能进入 repair。
    assert "c.repair_lesson.v1" not in service.calls


def test_model_success_roundtrips_with_program_generated_citation() -> None:
    service = ScriptedService(write=_draft(), review=_pass_review())

    draft, limitations = _generate(service)

    assert isinstance(draft, LessonDraft)
    assert limitations == []
    # 可持久化：LessonDraft 必须能按 JSON 往返。
    persisted = LessonDraft.model_validate(draft.model_dump(mode="json"))
    assert persisted == draft
    citations = [item for item in draft.components if item.type == "citation"]
    assert len(citations) == 1
    labels = [label.label for label in citations[0].labels]  # type: ignore[union-attr]
    assert any("kp-T02I1" in label and "kn-1" in label for label in labels)
    assert not any("http" in label or "页" in label for label in labels)


def test_lesson_payload_defaults_to_rule_and_only_model_when_passed() -> None:
    draft = build_rule_lesson(
        title="运用等式性质解一元一次方程",
        goal="会移项变号",
        kp_id="kp-T02I1",
        evidence_refs=_refs(),
        teaching_points=ERROR_POINTS,
    )
    rule_review = review_info_for(draft, validate_lesson(draft), method="rule")
    assert rule_review.method == "rule"

    rule_payload = lesson_payload(
        draft,
        review=rule_review,
        origin_plan_version="plan-1",
        content_id="lesson-1",
        content_version="lessonv-1",
    )
    assert rule_payload["creation_method"] == "rule"
    assert rule_payload["review_info"]["method"] == "rule"

    model_payload = lesson_payload(
        draft,
        review=review_info_for(draft, validate_lesson(draft), method="model"),
        origin_plan_version="plan-1",
        content_id="lesson-1",
        content_version="lessonv-1",
        creation_method="model",
    )
    assert model_payload["creation_method"] == "model"
    assert model_payload["review_info"]["method"] == "model"

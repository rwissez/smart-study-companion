"""解答题评分：缺分项分时转待复核，不冒充完整评分。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.analysis.grading import grade_solution_with_model
from study_companion.contracts import Attempt, Material, RubricPoint


class FakeService:
    def __init__(self, value) -> None:  # noqa: ANN001
        self._value = value

    def generate_structured(self, request, schema):  # noqa: ANN001
        @dataclass
        class Result:
            succeeded: bool
            value: object
            error: dict | None

        return Result(True, self._value, None)


def _attempt() -> Attempt:
    return Attempt(
        attempt_id="att-1",
        student_id="D01",
        dataset_id="ds-1",
        plan_version="plan-1",
        activity_id="act-1",
        material_id="Q1",
        final_answer="4x+6",
        solution_steps="3(x+2)+x = 3x+6+x = 4x+6",
        status="submitted",
    )


def _material() -> Material:
    return Material(
        material_id="Q1",
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=["T01I1"],
        content="化简 3(x+2)+x",
        reference_answer="4x+6",
        reference_explanation="去括号后合并同类项",
        max_score=3,
        rubric_points=[
            RubricPoint(point_id="p1", description="去括号", max_score=1),
            RubricPoint(point_id="p2", description="合并同类项", max_score=2),
        ],
        question_type="solution",
        source_locator={
            "source_file": "unit",
            "source_kind": "standard",
            "verification_status": "verified",
        },
    )


def test_missing_rubric_scores_goes_to_review() -> None:
    class Value:
        score = 3
        rubric_scores = []
        feedback = ""

    result = grade_solution_with_model(
        _attempt(), _material(), grade_id="g1", revision=1, service=FakeService(Value())
    )
    assert result.status == "needs_review"
    assert result.score is None
    assert "分项分" in result.limitations[0]


def test_rubric_scores_must_sum_to_total() -> None:
    class Item:
        point_id = "p1"
        score = 1

    class Value:
        score = 3
        rubric_scores = [Item()]
        feedback = ""

    result = grade_solution_with_model(
        _attempt(), _material(), grade_id="g2", revision=1, service=FakeService(Value())
    )
    assert result.status == "needs_review"
    assert result.score is None

"""解答题独立复核：一致接受、不一致转待复核、复核失败 fail-closed、越界拒绝、规则评分不受影响。

全部使用 FakeService，不调用真实模型。
"""

from __future__ import annotations

from typing import Any

from study_companion.analysis.grading import (
    MAX_MODEL_CALLS,
    PROPOSAL_TASK_KEY,
    REVIEW_TASK_KEY,
    GradingReviewOut,
    GradingReviewPointOut,
    grade_attempt,
    grade_attempt_with_review,
)
from study_companion.contracts import Attempt, GradingResult, Material, RubricPoint


class _Result:
    def __init__(
        self,
        succeeded: bool,
        value: Any = None,
        error: dict | None = None,
    ) -> None:
        self.succeeded = succeeded
        self.value = value
        self.error = error


class FakeGradingService:
    """按任务键返回预设结果的模型服务替身，记录每次请求。"""

    configured = True

    def __init__(
        self,
        *,
        proposal: Any = None,
        review: Any = None,
        proposal_failure: str | None = None,
        review_failure: str | None = None,
        review_exception: Exception | None = None,
    ) -> None:
        self.proposal = proposal
        self.review = review
        self.proposal_failure = proposal_failure
        self.review_failure = review_failure
        self.review_exception = review_exception
        self.requests: list[Any] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.requests.append(request)
        if request.task_key == PROPOSAL_TASK_KEY:
            if self.proposal_failure:
                return _Result(
                    False,
                    None,
                    {"code": self.proposal_failure, "message": "初评失败", "details": []},
                )
            return _Result(True, self.proposal)
        if request.task_key == REVIEW_TASK_KEY:
            if self.review_exception is not None:
                raise self.review_exception
            if self.review_failure:
                return _Result(
                    False,
                    None,
                    {"code": self.review_failure, "message": "复核失败", "details": []},
                )
            return _Result(True, self.review)
        raise AssertionError(f"未预期的模型任务：{request.task_key}")

    @property
    def task_keys(self) -> list[str]:
        return [request.task_key for request in self.requests]


class _ProposalPoint:
    def __init__(self, point_id: str, score: int) -> None:
        self.point_id = point_id
        self.score = score


class _Proposal:
    """初评输出替身；字段与 ``_SolutionGradeOut`` 对齐，测试不经过真实模型。"""

    def __init__(
        self,
        *,
        score: int | None,
        points: list[tuple[str, int]],
        limitations: list[str] | None = None,
    ) -> None:
        self.status = "proposed"
        self.score = score
        self.rubric_scores = [_ProposalPoint(point_id, value) for point_id, value in points]
        self.limitations = limitations or []


def _review(
    *,
    decision: str = "pass",
    total_score: int = 3,
    points: list[tuple[str, str, int, str, list[str]]] | None = None,
    checks: list[str] | None = None,
    missing_evidence: list[str] | None = None,
) -> GradingReviewOut:
    if points is None:
        points = [
            ("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"]),
            ("p2", "agree", 2, "合并同类项正确", ["3x+6+x = 4x+6"]),
        ]
    return GradingReviewOut(
        decision=decision,
        total_score=total_score,
        point_reviews=[
            GradingReviewPointOut(
                point_id=point_id,
                decision=point_decision,
                score=score,
                reason=reason,
                evidence=evidence,
            )
            for point_id, point_decision, score, reason, evidence in points
        ],
        checks=checks or ["分项覆盖完整", "总分与分项一致"],
        missing_evidence=missing_evidence or [],
    )


def _attempt(question_type: str = "solution") -> Attempt:
    answer = "B" if question_type == "choice" else "4x+6"
    return Attempt(
        attempt_id="att-1",
        student_id="D01",
        dataset_id="ds-1",
        plan_version="plan-1",
        activity_id="act-1",
        material_id="Q1",
        final_answer=answer,
        solution_steps="3(x+2)+x = 3x+6+x = 4x+6",
        status="submitted",
    )


def _material(question_type: str = "solution") -> Material:
    base: dict[str, Any] = {
        "material_id": "Q1",
        "material_type": "question",
        "topic_ids": ["T01"],
        "indicator_ids": ["T01I1"],
        "content": "化简 3(x+2)+x",
        "reference_answer": "4x+6" if question_type != "choice" else "B",
        "reference_explanation": "去括号后合并同类项",
        "max_score": 3 if question_type == "solution" else 2,
        "source_locator": {
            "source_file": "unit",
            "source_kind": "standard",
            "verification_status": "verified",
        },
        "question_type": question_type,
    }
    if question_type == "solution":
        base["rubric_points"] = [
            RubricPoint(point_id="p1", description="去括号", max_score=1),
            RubricPoint(point_id="p2", description="合并同类项", max_score=2),
        ]
    if question_type == "choice":
        base["options"] = ["3x+6", "4x+6", "3x+2", "x+6"]
    return Material(**base)


def test_review_agreement_accepts_grade() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)], limitations=["初评说明"]),
        review=_review(),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g1", revision=1, service=service
    )

    assert outcome.accepted is True
    assert outcome.grade.status == "graded"
    assert outcome.grade.validation_status == "validated"
    assert outcome.grade.score == 3
    assert [(item.point_id, item.score) for item in outcome.grade.rubric_scores] == [
        ("p1", 1),
        ("p2", 2),
    ]
    assert outcome.review is not None and outcome.review.decision == "pass"
    assert outcome.model_calls == 2
    assert outcome.model_calls <= MAX_MODEL_CALLS
    assert service.task_keys == [PROPOSAL_TASK_KEY, REVIEW_TASK_KEY]

    # 复核请求只带原始作答与候选分项分，不复用初评理由或限制。
    review_payload = service.requests[1].input_payload
    assert "limitations" not in review_payload
    assert review_payload["candidate"] == {
        "total_score": 3,
        "points": [
            {"point_id": "p1", "awarded_score": 1},
            {"point_id": "p2", "awarded_score": 2},
        ],
    }
    assert all("reason" not in point for point in review_payload["candidate"]["points"])


def test_review_disagreement_goes_to_needs_review() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review=_review(
            decision="repair",
            total_score=2,
            points=[
                ("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"]),
                ("p2", "disagree", 1, "合并同类项只完成一半", ["3x+6+x"]),
            ],
        ),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g2", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.validation_status == "not_checked"
    assert outcome.grade.score is None
    # 初评分项作为记录保留，不被复核分数覆盖。
    assert [(item.point_id, item.score) for item in outcome.grade.rubric_scores] == [
        ("p1", 1),
        ("p2", 2),
    ]
    assert outcome.review is not None and outcome.review.decision == "repair"
    joined = " ".join(outcome.grade.limitations)
    assert "复核未通过" in joined
    assert "p2" in joined and "1" in joined and "2" in joined
    # 待复核结果仍满足 GradingResult 契约：没有分数。
    GradingResult.model_validate(outcome.grade.model_dump(mode="json"))
    assert outcome.model_calls == 2


def test_review_failure_is_fail_closed() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review_failure="MODEL_TIMEOUT",
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g3", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.review is None
    assert outcome.review_error == "MODEL_TIMEOUT"
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.score is None
    assert "复核未成功" in outcome.grade.limitations[0]
    assert "不自动通过" in outcome.grade.limitations[0]
    assert outcome.model_calls == 2


def test_review_exception_is_fail_closed() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review_exception=RuntimeError("适配层异常"),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g4", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.review is None
    assert outcome.review_error is not None and outcome.review_error.startswith("EXCEPTION:")
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.score is None


def test_review_point_score_over_point_max_is_rejected() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review=_review(
            total_score=4,
            points=[
                ("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"]),
                ("p2", "agree", 3, "合并同类项给满分", ["3x+6+x = 4x+6"]),
            ],
        ),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g5", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.validation_status == "rejected"
    assert outcome.grade.score is None
    joined = " ".join(outcome.grade.limitations)
    assert "超出该点满分" in joined


def test_review_total_over_max_score_is_rejected() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review=_review(
            total_score=5,
            points=[
                ("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"]),
                ("p2", "agree", 2, "合并同类项正确", ["3x+6+x = 4x+6"]),
            ],
        ),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g6", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.validation_status == "rejected"
    assert outcome.grade.score is None
    joined = " ".join(outcome.grade.limitations)
    assert "总分上限" in joined


def test_review_must_cover_every_rubric_point() -> None:
    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review=_review(
            total_score=1,
            points=[("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"])],
        ),
    )
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g7", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.grade.status == "needs_review"
    assert "未覆盖评分点" in " ".join(outcome.grade.limitations)


def test_proposal_failure_skips_review() -> None:
    service = FakeGradingService(proposal_failure="MODEL_TIMEOUT")
    outcome = grade_attempt_with_review(
        _attempt(), _material(), grade_id="g8", revision=1, service=service
    )

    assert outcome.accepted is False
    assert outcome.review is None
    assert outcome.grade.status == "needs_review"
    assert outcome.grade.score is None
    assert service.task_keys == [PROPOSAL_TASK_KEY]
    assert outcome.model_calls == 1


def test_rule_grading_is_unaffected() -> None:
    service = FakeGradingService()  # 任何模型调用都会触发未预期任务断言

    choice = grade_attempt(
        _attempt("choice"),
        _material("choice"),
        grade_id="g9",
        revision=1,
        service=service,
    )
    assert choice.status == "graded"
    assert choice.score == 2
    assert choice.method == "rule"
    assert service.task_keys == []

    fill = grade_attempt(
        _attempt("fill_blank"),
        _material("fill_blank"),
        grade_id="g10",
        revision=1,
        service=service,
    )
    assert fill.status == "graded"
    assert fill.score == 2
    assert service.task_keys == []

    without_model = grade_attempt(
        _attempt(), _material(), grade_id="g11", revision=1, service=None
    )
    assert without_model.status == "needs_review"
    assert without_model.score is None
    assert without_model.method == "rule"


def test_grade_attempt_entry_applies_review() -> None:
    """正式入口 grade_attempt 也走复核：不一致时不得直接给分。"""

    service = FakeGradingService(
        proposal=_Proposal(score=3, points=[("p1", 1), ("p2", 2)]),
        review=_review(
            decision="uncertain",
            total_score=2,
            points=[
                ("p1", "agree", 1, "去括号正确", ["3(x+2) = 3x+6"]),
                ("p2", "uncertain", 1, "过程无法确认", ["3x+6+x"]),
            ],
        ),
    )
    result = grade_attempt(
        _attempt(), _material(), grade_id="g12", revision=1, service=service
    )

    assert result.status == "needs_review"
    assert result.score is None
    assert result.validation_status == "not_checked"
    assert service.task_keys == [PROPOSAL_TASK_KEY, REVIEW_TASK_KEY]

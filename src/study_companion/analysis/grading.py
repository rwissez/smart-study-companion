"""在线作答评分（01 §7.1、03 §14）。

分层：
* 选择题：规则核对选项；
* 填空题：规则归一 + 等价表达检查；
* 解答题：模型按参考答案与评分点分点评分，程序再验范围、分项之和与真实作答依据。

不可靠或未配置模型时返回 ``needs_review``/``pending`` 且 **没有分数**，
绝不把"没评"当 0 分，也不伪造评分理由。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from study_companion.contracts import (
    Attempt,
    DifficultyAssessment,
    DomainError,
    GradingResult,
    Material,
    RubricScore,
    canonical_json,
)
from study_companion.model_service import ModelService, ModelTaskRequest

RULE_VERSION = "grading-v1"

OPTION_LETTERS = "ABCDEFGH"


class _RubricOut(BaseModel):
    point_id: str
    score: int = Field(ge=0)
    reason: str


class _SolutionGradeOut(BaseModel):
    status: str
    score: int | None = None
    rubric_scores: list[_RubricOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("，", ",").replace("。", ".")
    return re.sub(r"\s+", "", normalized).lower()


def _numeric_value(text: str) -> float | None:
    cleaned = _normalise(text).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _choice_letter(answer: str | None, options: list[str]) -> str | None:
    if not answer:
        return None
    text = _normalise(answer)
    if text and text[0] in OPTION_LETTERS.lower():
        return text[0].upper()
    for index, option in enumerate(options):
        if _normalise(option) == text:
            return OPTION_LETTERS[index]
    return None


def grade_by_rule(
    attempt: Attempt, material: Material, *, grade_id: str, revision: int
) -> GradingResult:
    max_score = material.max_score or 0
    answer = attempt.final_answer
    if attempt.status == "in_progress":
        raise DomainError("UNPROCESSABLE", "作答尚未提交，不能评分")
    if not answer and not attempt.solution_steps:
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="unanswered",
            validation_status="validated",
            score=None,
            max_score=max_score,
            rubric_scores=[],
            method="rule",
            evidence_refs=[],
            limitations=["学生标记为未作答，不产生有效分，也不按 0 分处理"],
            rule_version=RULE_VERSION,
        )

    if material.question_type == "choice":
        expected = _choice_letter(material.reference_answer, material.options)
        got = _choice_letter(answer, material.options)
        if expected is None:
            return GradingResult(
                grade_id=grade_id,
                attempt_id=attempt.attempt_id,
                revision=revision,
                status="needs_review",
                validation_status="not_checked",
                score=None,
                max_score=max_score,
                rubric_scores=[],
                method="rule",
                evidence_refs=[],
                limitations=["参考答案不是可识别的选项，需教师确认评分口径"],
                rule_version=RULE_VERSION,
            )
        correct = got == expected
        score = max_score if correct else 0
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="graded",
            validation_status="validated",
            score=score,
            max_score=max_score,
            rubric_scores=[],
            method="rule",
            evidence_refs=[],
            limitations=[] if correct else ["选择题答案与参考答案不一致"],
            rule_version=RULE_VERSION,
        )

    if material.question_type == "fill_blank":
        expected = _normalise(material.reference_answer)
        got = _normalise(answer)
        if not expected:
            return GradingResult(
                grade_id=grade_id,
                attempt_id=attempt.attempt_id,
                revision=revision,
                status="needs_review",
                validation_status="not_checked",
                score=None,
                max_score=max_score,
                rubric_scores=[],
                method="rule",
                evidence_refs=[],
                limitations=["参考答案为空，不能自动评分"],
                rule_version=RULE_VERSION,
            )
        numeric_expected = _numeric_value(expected)
        numeric_got = _numeric_value(got)
        correct = got == expected or (
            numeric_expected is not None
            and numeric_got is not None
            and abs(numeric_expected - numeric_got) < 1e-9
        )
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="graded",
            validation_status="validated",
            score=max_score if correct else 0,
            max_score=max_score,
            rubric_scores=[],
            method="rule",
            evidence_refs=[],
            limitations=[] if correct else ["填空题答案与参考答案不等价"],
            rule_version=RULE_VERSION,
        )

    # 解答题：规则无法可靠判定分项，交给模型评分或待复核。
    return GradingResult(
        grade_id=grade_id,
        attempt_id=attempt.attempt_id,
        revision=revision,
        status="needs_review",
        validation_status="not_checked",
        score=None,
        max_score=max_score,
        rubric_scores=[],
        method="rule",
        evidence_refs=[],
        limitations=["解答题需要按评分点逐项评分，规则层不给出分数"],
        rule_version=RULE_VERSION,
    )


def grade_solution_with_model(
    attempt: Attempt,
    material: Material,
    *,
    grade_id: str,
    revision: int,
    service: ModelService,
) -> GradingResult:
    max_score = material.max_score or 0
    rubric = [
        {"point_id": point.point_id, "description": point.description, "max_score": point.max_score}
        for point in material.rubric_points
    ]
    request = ModelTaskRequest(
        task_key="b.grade_solution.v1",
        prompt_version="1.0",
        namespace="grading",
        input_payload={
            "question": material.content,
            "reference_answer": material.reference_answer,
            "reference_explanation": material.reference_explanation,
            "rubric_points": rubric,
            "max_score": max_score,
            "student_answer": attempt.final_answer,
            "student_steps": attempt.solution_steps,
            "attempt_id": attempt.attempt_id,
        },
        allow_external=False,
    )
    result = service.generate_structured(request, _SolutionGradeOut)
    if not result.succeeded or result.value is None:
        error = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="needs_review",
            validation_status="not_checked",
            score=None,
            max_score=max_score,
            rubric_scores=[],
            method="model",
            evidence_refs=[],
            limitations=[f"模型评分未成功（{error}），已保留作答等待复核"],
            rule_version=RULE_VERSION,
            prompt_version=request.prompt_version,
        )
    value = result.value
    scores: list[RubricScore] = []
    valid_points = {point.point_id: point.max_score for point in material.rubric_points}
    for item in value.rubric_scores:
        if item.point_id not in valid_points:
            return GradingResult(
                grade_id=grade_id,
                attempt_id=attempt.attempt_id,
                revision=revision,
                status="needs_review",
                validation_status="rejected",
                score=None,
                max_score=max_score,
                rubric_scores=[],
                method="model",
                evidence_refs=[],
                limitations=["模型返回了不存在的评分点，评分不可用"],
                rule_version=RULE_VERSION,
            )
        if item.score > valid_points[item.point_id]:
            return GradingResult(
                grade_id=grade_id,
                attempt_id=attempt.attempt_id,
                revision=revision,
                status="needs_review",
                validation_status="rejected",
                score=None,
                max_score=max_score,
                rubric_scores=[],
                method="model",
                evidence_refs=[],
                limitations=["模型给出的分项分超出该项满分，评分不可用"],
                rule_version=RULE_VERSION,
            )
        scores.append(RubricScore(point_id=item.point_id, score=item.score, max_score=valid_points[item.point_id]))

    if material.rubric_points and not scores:
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="needs_review",
            validation_status="not_checked",
            score=None,
            max_score=max_score,
            rubric_scores=[],
            method="model",
            evidence_refs=[],
            limitations=["模型未返回分项分，解答题评分不完整，保留作答等待复核"],
            rule_version=RULE_VERSION,
        )
    total = value.score if value.score is not None else sum(item.score for item in scores)
    if total < 0 or total > max_score:
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="needs_review",
            validation_status="rejected",
            score=None,
            max_score=max_score,
            rubric_scores=[],
            method="model",
            evidence_refs=[],
            limitations=["模型总分超出允许范围，评分不可用"],
            rule_version=RULE_VERSION,
        )
    if scores and all(point.max_score for point in scores) and sum(item.score for item in scores) != total:
        return GradingResult(
            grade_id=grade_id,
            attempt_id=attempt.attempt_id,
            revision=revision,
            status="needs_review",
            validation_status="rejected",
            score=None,
            max_score=max_score,
            rubric_scores=[],
            method="model",
            evidence_refs=[],
            limitations=["分项之和与总分不一致，评分不可用"],
            rule_version=RULE_VERSION,
        )
    return GradingResult(
        grade_id=grade_id,
        attempt_id=attempt.attempt_id,
        revision=revision,
        status="graded",
        validation_status="validated",
        score=int(total),
        max_score=max_score,
        rubric_scores=scores,
        method="model",
        evidence_refs=[],
        limitations=value.limitations,
        rule_version=RULE_VERSION,
        prompt_version=request.prompt_version,
    )


def grade_attempt(
    attempt: Attempt,
    material: Material,
    *,
    grade_id: str,
    revision: int,
    service: ModelService | None = None,
) -> GradingResult:
    """先规则、后模型；解答题在无模型时明确待复核。"""

    result = grade_by_rule(attempt, material, grade_id=grade_id, revision=revision)
    if result.status == "needs_review" and material.question_type == "solution" and service is not None:
        return grade_solution_with_model(
            attempt, material, grade_id=grade_id, revision=revision, service=service
        )
    return result


def difficulty_payload(assessment: DifficultyAssessment | None) -> dict[str, Any] | None:
    return assessment.model_dump(mode="json") if assessment else None

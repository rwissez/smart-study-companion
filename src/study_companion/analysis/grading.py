"""在线作答评分（01 §7.1、03 §6.8、03 §7.5、03 §14）。

分层：
* 选择题：规则核对选项；
* 填空题：规则归一 + 等价表达检查；
* 解答题：模型按参考答案与评分点分点评分（``grading_proposal``），
  再由第二次独立调用复核（``grading_review``），程序比较两次的总分与分项分，
  一致才接受，否则转 ``needs_review``。

复核只接收原始作答与候选分项分数，不接收初评理由，避免复用第一遍解释。
每次评分最多两次模型调用（初评 + 复核）：初评失败不再复核，复核失败转待复核，
绝不退回自动通过；模型给的分项分不得超过评分点满分，也不能改写满分上限。

不可靠或未配置模型时返回 ``needs_review``/``pending`` 且 **没有分数**，
绝不把"没评"当 0 分，也不伪造评分理由。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

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
REVIEW_RULE_VERSION = "grading-review-v1"

#: 独立复核任务：与初评分开调用，只带原始作答与候选分项分数。
REVIEW_TASK_KEY = "grading_review.v1"
REVIEW_PROMPT_VERSION = "1.0"
REVIEW_PROMPT_NAMESPACE = "b"
PROPOSAL_TASK_KEY = "b.grade_solution.v1"

#: 03 §7.5：只能使用同一模型时，用独立审核提示并记录独立性等级。
REVIEW_INDEPENDENCE = "same_model_independent_prompt"

#: 一次解答题评分最多两次模型调用：初评一次、复核一次。
#: 这里指评分任务请求；ModelService 对 JSON 结构错误的单次重试属于传输层修复，
#: 不会产生第三次评分、第三次复核或自动改分。
MAX_MODEL_CALLS = 2

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


class GradingReviewPointOut(BaseModel):
    """复核模型对单个评分点的独立意见（03 §14.6）。"""

    point_id: str
    decision: Literal["agree", "disagree", "uncertain"]
    score: int = Field(ge=0)
    reason: str
    evidence: list[str] = Field(default_factory=list)


class GradingReviewOut(BaseModel):
    """复核模型的结构化输出；满分上限不在 schema 内，模型不能改写。"""

    decision: Literal["pass", "repair", "reject", "uncertain"]
    total_score: int = Field(ge=0)
    point_reviews: list[GradingReviewPointOut] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


ReviewDecision = Literal["pass", "repair", "reject", "uncertain"]


@dataclass(frozen=True)
class ReviewPointOpinion:
    """复核对单个评分点的意见；score 是复核自己给出的分，不是初评分。"""

    point_id: str
    decision: Literal["agree", "disagree", "uncertain"]
    score: int
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GradingReview:
    """程序校验通过的复核结果；失败时不存在本对象，由 ``review_error`` 表示。"""

    decision: ReviewDecision
    total_score: int
    points: tuple[ReviewPointOpinion, ...] = ()
    checks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    prompt_version: str = REVIEW_PROMPT_VERSION
    rule_version: str = REVIEW_RULE_VERSION
    independence: str = REVIEW_INDEPENDENCE


@dataclass
class GradingOutcome:
    """一次评分编排的结果：正式评分 + 复核记录 + 模型调用次数。

    ``grade`` 是唯一对外写入的 ``GradingResult``；``review`` 保留结构化复核意见，
    复核失败时为 ``None`` 且 ``review_error`` 给出错误码。

    当前 ``GradingResult`` 契约没有复核字段，复核要点（decision、两次总分、
    逐点意见与证据）同时写入 ``grade.limitations``，随 ``Grade.payload_json``
    持久化；待 01 扩展契约后再改为正式字段。
    """

    grade: GradingResult
    review: GradingReview | None = None
    review_error: str | None = None
    model_calls: int = 0
    accepted: bool = False


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
    """初评（``grading_proposal``）：一次模型调用，只验自身结构，不判定可接受。

    可接受性由 :func:`review_solution_candidate` 的独立复核与程序比较决定；
    本函数单独使用时仍保持旧行为，供只做提案的调用方使用。
    """

    max_score = material.max_score or 0
    rubric = [
        {"point_id": point.point_id, "description": point.description, "max_score": point.max_score}
        for point in material.rubric_points
    ]
    request = ModelTaskRequest(
        task_key=PROPOSAL_TASK_KEY,
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
        allow_external=True,
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


# ---------------------------------------------------------------------------
# 独立复核（03 §6.8、03 §7.5、03 §14.6）
# ---------------------------------------------------------------------------


def _review_input_payload(
    attempt: Attempt, material: Material, candidate: GradingResult
) -> dict[str, Any]:
    """复核输入：只给原始作答与候选分项分，不给初评理由，避免复用第一遍解释。"""

    return {
        "question": material.content,
        "reference_answer": material.reference_answer,
        "reference_explanation": material.reference_explanation,
        "rubric_points": [
            {
                "point_id": point.point_id,
                "description": point.description,
                "max_score": point.max_score,
            }
            for point in material.rubric_points
        ],
        "max_score": material.max_score or 0,
        "student_answer": attempt.final_answer,
        "student_steps": attempt.solution_steps,
        "candidate": {
            "total_score": candidate.score,
            "points": [
                {"point_id": item.point_id, "awarded_score": item.score}
                for item in candidate.rubric_scores
            ],
        },
        "attempt_id": attempt.attempt_id,
    }


def review_solution_candidate(
    attempt: Attempt,
    material: Material,
    candidate: GradingResult,
    *,
    service: ModelService,
) -> tuple[GradingReview | None, str | None]:
    """第二次独立调用，复核候选分项分是否有作答证据支持。

    返回 ``(review, error_code)``。调用失败、超时或输出不合法时 ``review`` 为
    ``None``、``error_code`` 有值，由调用方转待复核，不允许退回自动通过。
    满分上限不进入模型 schema，模型只能给出分项分与总分。
    """

    request = ModelTaskRequest(
        task_key=REVIEW_TASK_KEY,
        prompt_version=REVIEW_PROMPT_VERSION,
        namespace=REVIEW_PROMPT_NAMESPACE,
        input_payload=_review_input_payload(attempt, material, candidate),
        allow_external=True,
    )
    try:
        result = service.generate_structured(request, GradingReviewOut)
    except Exception as exc:  # 模型适配层异常一律 fail-closed
        return None, f"EXCEPTION:{type(exc).__name__}"
    if not getattr(result, "succeeded", False) or getattr(result, "value", None) is None:
        error = getattr(result, "error", None) or {}
        return None, str(error.get("code") or "MODEL_BAD_RESPONSE")
    value = result.value
    review = GradingReview(
        decision=value.decision,
        total_score=int(value.total_score),
        points=tuple(
            ReviewPointOpinion(
                point_id=item.point_id,
                decision=item.decision,
                score=int(item.score),
                reason=item.reason,
                evidence=tuple(
                    evidence for evidence in item.evidence if evidence.strip()
                ),
            )
            for item in value.point_reviews
        ),
        checks=tuple(value.checks),
        missing_evidence=tuple(value.missing_evidence),
        limitations=tuple(value.limitations),
    )
    return review, None


def _review_problems(
    material: Material, candidate: GradingResult, review: GradingReview
) -> tuple[list[str], bool]:
    """比较两次总分与分项分；返回 (问题列表, 是否硬拒绝)，空列表才可接受。"""

    max_score = material.max_score or 0
    point_max = {point.point_id: point.max_score for point in material.rubric_points}
    candidate_scores = {item.point_id: item.score for item in candidate.rubric_scores}
    problems: list[str] = []
    rejected = False

    seen: set[str] = set()
    review_scores: dict[str, int] = {}
    for item in review.points:
        if item.point_id not in point_max:
            problems.append(f"复核返回了不存在的评分点 {item.point_id}")
            rejected = True
            continue
        if item.point_id in seen:
            problems.append(f"复核对评分点 {item.point_id} 给了多条意见")
            rejected = True
            continue
        seen.add(item.point_id)
        review_scores[item.point_id] = item.score
        if item.score > point_max[item.point_id]:
            problems.append(
                f"复核给评分点 {item.point_id} 的分数 {item.score} 超出该点满分 "
                f"{point_max[item.point_id]}，不得改写评分点满分"
            )
            rejected = True

    missing = [point_id for point_id in point_max if point_id not in seen]
    if missing:
        problems.append("复核未覆盖评分点：" + "、".join(missing))

    uncovered = [point_id for point_id in point_max if point_id not in candidate_scores]
    if uncovered:
        problems.append("初评分项覆盖不完整，缺少评分点：" + "、".join(uncovered))

    review_sum = sum(review_scores.values())
    if review_sum > max_score:
        problems.append(f"复核分项之和 {review_sum} 超过满分 {max_score}，不得改写总分上限")
        rejected = True
    if review.total_score > max_score:
        problems.append(f"复核总分 {review.total_score} 超过满分 {max_score}，不得改写总分上限")
        rejected = True
    if review.total_score != review_sum:
        problems.append(f"复核总分 {review.total_score} 与复核分项之和 {review_sum} 不一致")
    if candidate.score is None or review.total_score != candidate.score:
        problems.append(f"两次评分总分不一致：初评 {candidate.score}，复核 {review.total_score}")

    for point_id, awarded in candidate_scores.items():
        if point_id not in point_max:
            problems.append(f"初评返回了不存在的评分点 {point_id}")
            rejected = True
            continue
        opinion = next((item for item in review.points if item.point_id == point_id), None)
        if opinion is None:
            continue
        if opinion.score != awarded:
            problems.append(
                f"评分点 {point_id} 两次分数不一致：初评 {awarded}，复核 {opinion.score}"
            )
        if opinion.decision != "agree":
            problems.append(f"复核对评分点 {point_id} 的结论是 {opinion.decision}：{opinion.reason}")
        if opinion.decision == "agree" and awarded > 0 and not opinion.evidence:
            problems.append(f"复核未给出评分点 {point_id} 的作答证据")

    if review.decision != "pass":
        problems.append(f"复核结论为 {review.decision}，不能自动接受")
    return problems, rejected


def _review_detail(review: GradingReview) -> str:
    """把复核意见压成一行可持久化说明，保留逐点意见与证据原文。"""

    parts: list[str] = []
    for item in review.points:
        text = f"{item.point_id}={item.score}分/{item.decision}：{item.reason}"
        if item.evidence:
            text += f"（证据：{'；'.join(item.evidence)}）"
        parts.append(text)
    if review.missing_evidence:
        parts.append("缺证据：" + "；".join(review.missing_evidence))
    if review.limitations:
        parts.append("复核限制：" + "；".join(review.limitations))
    detail = f"复核总分 {review.total_score}"
    if parts:
        detail += "；" + " | ".join(parts)
    return detail


def _apply_review(
    material: Material,
    candidate: GradingResult,
    review: GradingReview | None,
    review_error: str | None,
) -> tuple[GradingResult, bool]:
    """按程序规则决定接受或转待复核；初评分数只在两次一致时采用。"""

    if review is None:
        limitations = [
            f"独立复核未成功（{review_error or 'MODEL_BAD_RESPONSE'}，"
            f"{REVIEW_TASK_KEY}@{REVIEW_PROMPT_VERSION}），初评总分 {candidate.score} "
            "未获独立确认，不自动通过；已保留原始作答与初评结果，转教师复核",
            *candidate.limitations,
        ]
        edited = candidate.model_copy(
            update={
                "status": "needs_review",
                "validation_status": "not_checked",
                "score": None,
                "limitations": limitations,
            }
        )
        return edited, False

    problems, rejected = _review_problems(material, candidate, review)
    if not problems:
        limitations = [
            *candidate.limitations,
            f"独立复核通过（{REVIEW_TASK_KEY}@{REVIEW_PROMPT_VERSION}，{review.independence}）",
        ]
        return candidate.model_copy(update={"limitations": limitations}), True

    limitations = [
        f"独立复核未通过（decision={review.decision}，"
        f"{REVIEW_TASK_KEY}@{REVIEW_PROMPT_VERSION}），初评总分 {candidate.score}、"
        f"复核总分 {review.total_score}，不自动采用任何一方分数；"
        "已保留原始作答、初评分项与复核意见，转教师复核",
        *candidate.limitations,
        *problems,
        _review_detail(review),
    ]
    edited = candidate.model_copy(
        update={
            "status": "needs_review",
            "validation_status": "rejected" if rejected else "not_checked",
            "score": None,
            "limitations": limitations,
        }
    )
    return edited, False


def grade_solution_with_review(
    attempt: Attempt,
    material: Material,
    *,
    grade_id: str,
    revision: int,
    service: ModelService,
) -> GradingOutcome:
    """解答题两阶段评分：初评 + 独立复核，两次一致才形成正式分数。

    初评失败时不再调用复核，模型调用次数为 1；复核失败或与初评不一致时
    转 ``needs_review``，保留原始作答与两次结果，调用次数最多为 2。
    """

    candidate = grade_solution_with_model(
        attempt, material, grade_id=grade_id, revision=revision, service=service
    )
    if candidate.status != "graded":
        return GradingOutcome(grade=candidate, model_calls=1)
    review, review_error = review_solution_candidate(
        attempt, material, candidate, service=service
    )
    grade, accepted = _apply_review(material, candidate, review, review_error)
    return GradingOutcome(
        grade=grade,
        review=review,
        review_error=review_error,
        model_calls=2,
        accepted=accepted,
    )


def grade_attempt_with_review(
    attempt: Attempt,
    material: Material,
    *,
    grade_id: str,
    revision: int,
    service: ModelService | None = None,
) -> GradingOutcome:
    """先规则、后模型、再独立复核；返回复核记录供审计，不改动正式评分入口。"""

    result = grade_by_rule(attempt, material, grade_id=grade_id, revision=revision)
    if (
        result.status == "needs_review"
        and material.question_type == "solution"
        and service is not None
    ):
        return grade_solution_with_review(
            attempt, material, grade_id=grade_id, revision=revision, service=service
        )
    return GradingOutcome(grade=result, model_calls=0)


def grade_attempt(
    attempt: Attempt,
    material: Material,
    *,
    grade_id: str,
    revision: int,
    service: ModelService | None = None,
) -> GradingResult:
    """先规则、后模型、再独立复核；对外仍只返回正式评分结果。"""

    outcome = grade_attempt_with_review(
        attempt, material, grade_id=grade_id, revision=revision, service=service
    )
    return outcome.grade


def difficulty_payload(assessment: DifficultyAssessment | None) -> dict[str, Any] | None:
    return assessment.model_dump(mode="json") if assessment else None

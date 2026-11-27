"""C 的模型协作：个性化微课堂生成与独立复核（04 §5、§6）。

三个质量底线：

* **个性化依据**：输入带 ``student_error_context``——该生这次的具体错误、错因、
  教师说明和本轮材料编号。区分 ``student_error``（具体错步）、
  ``student_diagnosis``（只有表现/错因类型）和 ``generic_knowledge``（通用讲解）；
  后两种不得写成个人错因。
* **可核验引用**：``citation`` 标签由程序从输入 ``EvidenceRef`` 生成，只写知识点
  编号/版本、诊断编号/分析版本或材料页标识；不接受模型自拟页码，不生成外部链接。
* **fail-closed 复核**：复核调用失败、结果不完整或任一项不是 ``pass``，一律退回
  规则模板并记录原因，绝不把模型草稿当正式内容返回。

``pipeline.attach_lessons`` 传入的 ``teaching_points`` 顺序为
``[知识依据定义, 学生引导语, 预期结果, 方案理由, 教师说明]``。第 0 项是知识点
定义，其余项才可能携带具体错误证据；规划器会把 B 的表现和错因类型写进方案理由，
供这里区分诊断层级。未来调用方也可直接传结构化的 ``student_error_context``、
``material_ids`` 和 ``student_id``。
"""

from __future__ import annotations

import re
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field

from study_companion.contracts import (
    CitationComponent,
    CitationLabel,
    EvidenceRef,
    LessonComponent,
    LessonDraft,
)
from study_companion.model_service import ModelService, ModelTaskRequest
from study_companion.recommendation.lesson import (
    DIAGNOSIS_REQUIREMENT,
    GENERIC_REQUIREMENT,
    PERSONALIZED_REQUIREMENT,
    build_student_error_context,
    is_diagnosis_only_lesson,
    is_generic_lesson,
)

PROMPT_VERSION = "1.2"
MAX_NOTE_CHARS = 300
MAX_CITATION_LABELS = 6

#: 独立复核必须逐项覆盖的检查（04 §5.2 微课堂八项的核心子集）。
REQUIRED_REVIEW_CHECKS: tuple[str, ...] = (
    "lesson_math",
    "lesson_steps",
    "lesson_personalization",
    "lesson_sources",
    "lesson_schema",
    "lesson_answer_guard",
)

#: 受限中间表达式的允许字符（04 §6.3 MathString）：数字、x/y/a/b、括号与运算符。
_ALLOWED_MATH_CHARS = re.compile(r"^[0-9xyab()+\-*/^=<>.\s]+$")
_RELATION = re.compile(r"<=|>=|(?<![<>=])=(?!=)|<(?![=])|>(?!=)")


class LessonReviewCheckOut(BaseModel):
    check_id: str = Field(min_length=1, max_length=64)
    result: Literal["pass", "fail", "uncertain"]
    reason: str = Field(default="", max_length=300)


class LessonModelOut(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=200)
    estimated_minutes: int = Field(default=3, ge=3, le=6)
    components: list[LessonComponent] = Field(min_length=1, max_length=12)
    fallback_text: str | None = Field(default=None, max_length=500)


class LessonReviewOut(BaseModel):
    decision: Literal["pass", "repair", "reject", "uncertain"]
    checks: list[LessonReviewCheckOut] = Field(default_factory=list, max_length=12)
    reasons: list[str] = Field(default_factory=list, max_length=8)
    issues: list[str] = Field(default_factory=list, max_length=8)


def _clean(text: str | None, limit: int = MAX_NOTE_CHARS) -> str:
    return (text or "").strip()[:limit]


def _normalize_error_context(
    context: dict[str, Any] | None,
    *,
    teaching_points,
    evidence_refs,
    knowledge_definition: str = "",
    material_ids: Sequence[str] = (),
    student_id: str | None = None,
) -> dict[str, Any]:
    """把显式上下文与教学要点合并；显式的通用/仅诊断标记优先，不被套话抬成个性化。"""

    merged = build_student_error_context(
        teaching_points=teaching_points,
        evidence_refs=evidence_refs,
        knowledge_definition=knowledge_definition,
        material_ids=material_ids,
        student_id=student_id,
    )
    if not context:
        return merged
    for key in ("student_error", "error_cause", "teacher_reason"):
        value = _clean(str(context.get(key) or ""))
        if value:
            merged[key] = value
    notes = context.get("observed_error_notes")
    if isinstance(notes, list) and notes:
        merged["observed_error_notes"] = [_clean(str(item)) for item in notes if _clean(str(item))][:4]
    performance = context.get("observed_performance_notes")
    if isinstance(performance, list) and performance:
        merged["observed_performance_notes"] = [
            _clean(str(item)) for item in performance if _clean(str(item))
        ][:4]
    extra_materials = context.get("material_ids")
    if isinstance(extra_materials, list):
        merged["material_ids"] = list(
            dict.fromkeys([*merged.get("material_ids", []), *(str(item) for item in extra_materials)])
        )
    explicit_personalized = context.get("personalized")
    explicit_basis = str(context.get("basis") or "")
    if explicit_personalized is True:
        merged["personalized"] = True
        merged["basis"] = "student_error"
        merged["requirement"] = PERSONALIZED_REQUIREMENT
    elif explicit_personalized is False or explicit_basis in {"generic_knowledge", "student_diagnosis"}:
        basis = explicit_basis or ("student_diagnosis" if merged["basis"] != "generic_knowledge" else "generic_knowledge")
        merged["personalized"] = False
        merged["basis"] = basis
        merged["requirement"] = (
            DIAGNOSIS_REQUIREMENT if basis == "student_diagnosis" else GENERIC_REQUIREMENT
        )
    elif merged.get("student_error") or merged.get("observed_error_notes"):
        merged["personalized"] = True
        merged["basis"] = "student_error"
        merged["requirement"] = PERSONALIZED_REQUIREMENT
    elif merged.get("error_cause") or merged.get("observed_performance_notes"):
        merged["personalized"] = False
        merged["basis"] = "student_diagnosis"
        merged["requirement"] = DIAGNOSIS_REQUIREMENT
    if merged.get("basis") == "student_error" and not (
        merged.get("student_error") or merged.get("observed_error_notes")
    ):
        if merged.get("error_cause") or merged.get("observed_performance_notes"):
            merged["basis"] = "student_diagnosis"
            merged["requirement"] = DIAGNOSIS_REQUIREMENT
        else:
            merged["basis"] = "generic_knowledge"
            merged["requirement"] = GENERIC_REQUIREMENT
        merged["personalized"] = False
    return merged


def _evidence_label(ref: EvidenceRef, lesson_title: str) -> str:
    """把一条证据引用转成学生可读、可核验的标签；只使用输入字段，不补页码。"""

    if ref.kind == "knowledge_point":
        name = _clean(lesson_title, 80) or ref.id
        return f"知识点「{name}」（编号 {ref.id}，知识库版本 {ref.knowledge_version or '未标版本'}）"
    if ref.kind == "finding":
        return f"学情诊断 {ref.id}（分析版本 {ref.analysis_version or '未标版本'}）"
    if ref.kind == "attempt":
        return f"本次作答记录 {ref.id}"
    if ref.kind == "grade":
        return f"本次评分记录 {ref.id}"
    if ref.kind == "source_page":
        return f"教材原文页（材料 {ref.material_id or '未标材料'}，页标识 {ref.id}）"
    if ref.kind == "annotation":
        return f"教材注释 {ref.id}（知识库版本 {ref.knowledge_version or '未标版本'}）"
    if ref.kind == "lesson":
        return f"关联微课堂 {ref.id}"
    if ref.kind == "material":
        return f"课程材料 {ref.id}（目录版本 {ref.catalog_version or '未标版本'}）"
    if ref.kind == "recommendation":
        return f"学习方案依据 {ref.id}"
    if ref.kind == "rubric_point":
        return f"评分点 {ref.id}（材料 {ref.material_id or '未标材料'}）"
    return f"依据记录 {ref.id}"


def build_citation_labels(
    evidence_refs: Sequence[EvidenceRef],
    *,
    lesson_title: str = "",
    limit: int = MAX_CITATION_LABELS,
) -> list[CitationLabel]:
    """按证据顺序生成引用标签；``evidence_index`` 指向草稿的 ``evidence_refs``。"""

    labels: list[CitationLabel] = []
    seen: set[tuple[str, str]] = set()
    for index, ref in enumerate(evidence_refs):
        if len(labels) >= limit:
            break
        key = (ref.kind, ref.id)
        if key in seen:
            continue
        seen.add(key)
        text = _clean(_evidence_label(ref, lesson_title), 80)
        if not text:
            continue
        labels.append(CitationLabel(label=text, evidence_index=index))
    return labels


def attach_verifiable_citations(
    draft: LessonDraft,
    *,
    evidence_refs: Sequence[EvidenceRef],
    lesson_title: str | None = None,
) -> tuple[LessonDraft, list[str]]:
    """用程序生成的标签替换模型自拟引用；没有证据时不生成引用组件。"""

    labels = build_citation_labels(evidence_refs, lesson_title=lesson_title or draft.title)
    kept: list[LessonComponent] = [
        component for component in draft.components if component.type != "citation"
    ]
    if not labels:
        return draft.model_copy(update={"components": kept}), [
            "没有可核验的证据引用，本次微课堂未生成引用组件"
        ]
    used_ids = {component.id for component in kept}
    component_id = "source"
    suffix = 2
    while component_id in used_ids:
        component_id = f"source-{suffix}"
        suffix += 1
    component = CitationComponent(id=component_id, type="citation", labels=labels)
    return draft.model_copy(update={"components": [*kept, component]}), []


def _expression_errors(expr: str) -> list[str]:
    """确定性数学硬校验：只允许受限字符、单关系、0—3 常量指数。"""

    text = (expr or "").strip()
    if not text:
        return ["公式为空"]
    errors: list[str] = []
    if len(text) > 160:
        errors.append("表达式超过 160 字符")
    if not _ALLOWED_MATH_CHARS.match(text):
        errors.append("含受限表达式之外的字符（不允许 LaTeX 或中文算式）")
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                errors.append("括号不配对")
                break
    if depth > 0:
        errors.append("括号不配对")
    if "()" in text:
        errors.append("空括号")
    if len(_RELATION.findall(text)) > 1:
        errors.append("比较式最多一处关系")
    if text.endswith(("+", "-", "*", "/", "^", "=", "<", ">")):
        errors.append("表达式以运算符结尾")
    if "**" in text or "//" in text or "^^" in text:
        errors.append("连续运算符不合法")
    for match in re.finditer(r"\^(\(([^()]*)\)|[^\s()+\-*/^=<>]*)", text):
        inner = match.group(2)
        token = (inner if inner is not None else match.group(1) or "").strip()
        if token not in {"0", "1", "2", "3"}:
            errors.append("指数只允许 0—3 的常量")
            break
    return errors


def draft_math_errors(draft: LessonDraft) -> list[str]:
    """扫描草稿里所有数学字段，返回带组件编号的问题说明。"""

    findings: list[str] = []
    for component in draft.components:
        if component.type == "formula":
            findings.extend(
                f"{component.id}: {message}" for message in _expression_errors(component.expr)
            )
        elif component.type == "derivation":
            for step_index, step in enumerate(component.steps, start=1):
                findings.extend(
                    f"{component.id} 第 {step_index} 步: {message}"
                    for message in _expression_errors(step.expr)
                )
        elif component.type == "contrast":
            for field in ("before", "incorrect", "correct"):
                findings.extend(
                    f"{component.id}.{field}: {message}"
                    for message in _expression_errors(getattr(component, field))
                )
    return findings


def _review_failure_reason(review: LessonReviewOut) -> str:
    checks = {check.check_id: check for check in review.checks}
    missing = [check_id for check_id in REQUIRED_REVIEW_CHECKS if check_id not in checks]
    failing = [
        f"{check.check_id}:{check.result}" + (f"（{check.reason[:60]}）" if check.reason else "")
        for check in review.checks
        if check.result != "pass"
    ]
    parts: list[str] = []
    if missing:
        parts.append("缺少检查项 " + "、".join(missing))
    if failing:
        parts.append("未通过 " + "、".join(failing))
    if not parts and review.decision != "pass":
        parts.append(f"结论 {review.decision}")
    extra = list(review.reasons) + list(review.issues)
    if extra:
        parts.append("；".join(str(item) for item in extra)[:160])
    return "；".join(parts)[:240]


def generate_with_model(
    *,
    service: ModelService,
    title: str,
    goal: str,
    kp_id: str,
    evidence_refs: list[EvidenceRef],
    teaching_points: list[str],
    protected_texts: list[str],
    student_error_context: dict[str, Any] | None = None,
    student_error: str | None = None,
    error_cause: str | None = None,
    material_ids: Sequence[str] = (),
    student_id: str | None = None,
) -> tuple[LessonDraft | None, list[str]]:
    """返回 (草稿, 限制说明)。失败时草稿为 None，由调用方退回规则模板。

    ``student_error_context`` 是模型的主要个性化依据；没有具体错误时按
    ``student_diagnosis`` 或 ``generic_knowledge`` 明确记录，不接受模型把通用
    讲解写成个人错因。
    """

    merged_context = dict(student_error_context or {})
    if _clean(student_error):
        merged_context["student_error"] = _clean(student_error)
    if _clean(error_cause):
        merged_context["error_cause"] = _clean(error_cause)
    error_context = _normalize_error_context(
        merged_context or None,
        teaching_points=teaching_points,
        evidence_refs=evidence_refs,
        knowledge_definition=goal,
        material_ids=material_ids,
        student_id=student_id,
    )
    labels = build_citation_labels(evidence_refs, lesson_title=title)

    request = ModelTaskRequest(
        task_key="c.write_content.v1",
        prompt_version=PROMPT_VERSION,
        namespace="c",
        input_payload={
            "title": title,
            "goal": goal,
            "knowledge_point_id": kp_id,
            "student_error_context": error_context,
            "evidence_labels": [label.model_dump(mode="json") for label in labels],
            "material_ids": list(error_context.get("material_ids") or [])[:8],
            "teaching_points": [_clean(item) for item in teaching_points if item][:6],
            "protected_material_contents": [_clean(item) for item in protected_texts][:12],
            "citation_policy": (
                "引用只能使用 evidence_labels 中的 index 和 label；不得编造页码、"
                "教材版本或外部链接。"
            ),
        },
        allow_external=False,
    )
    try:
        result = service.generate_structured(request, LessonModelOut)
    except Exception as exc:  # 模型服务异常同样按失败处理
        return None, [f"模型生成微课堂失败（调用异常 {type(exc).__name__}），本轮使用规则模板"]
    if not result.succeeded or result.value is None:
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return None, [f"模型生成微课堂失败（{code}），本轮使用规则模板"]

    value = result.value
    draft = LessonDraft(
        title=value.title,
        goal=value.goal,
        estimated_minutes=value.estimated_minutes,
        kp_ids=[kp_id],
        evidence_refs=evidence_refs[:12],
        source_refs=[],
        components=value.components,
        fallback_text=value.fallback_text or goal,
    )

    math_errors = draft_math_errors(draft)
    if math_errors:
        return None, [
            "模型草稿数学表达式硬校验未通过（" + "；".join(math_errors)[:200] + "），本轮使用规则模板"
        ]

    basis = error_context.get("basis")
    if basis == "generic_knowledge" and not is_generic_lesson(draft):
        return None, [
            "模型草稿没有具体错误证据却写成个人错因，已退回规则模板"
        ]
    if basis == "student_diagnosis" and not (
        is_diagnosis_only_lesson(draft) or is_generic_lesson(draft)
    ):
        return None, [
            "模型草稿只有诊断表现却写成具体错步，已退回规则模板"
        ]

    review_request = ModelTaskRequest(
        task_key="c.review_lesson.v1",
        prompt_version=PROMPT_VERSION,
        namespace="c",
        input_payload={
            "draft": draft.model_dump(mode="json"),
            "knowledge_point_id": kp_id,
            "student_error_context": error_context,
            "evidence_labels": [label.model_dump(mode="json") for label in labels],
            "material_ids": list(error_context.get("material_ids") or [])[:8],
            "required_checks": list(REQUIRED_REVIEW_CHECKS),
            "protected_material_contents": [_clean(item) for item in protected_texts][:12],
        },
        allow_external=False,
    )
    try:
        review = service.generate_structured(review_request, LessonReviewOut)
    except Exception as exc:
        return None, [
            f"微课堂独立复核调用异常（{type(exc).__name__}），本轮改用规则模板"
        ]
    if not review.succeeded or review.value is None:
        code = (review.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return None, [f"微课堂独立复核未完成（{code}），本轮改用规则模板"]

    review_value = review.value
    if review_value.decision != "pass":
        return None, [
            "微课堂独立复核未通过（" + _review_failure_reason(review_value) + "），本轮使用规则模板"
        ]
    checks = {check.check_id: check for check in review_value.checks}
    if set(REQUIRED_REVIEW_CHECKS) - set(checks):
        return None, [
            "微课堂独立复核结果不完整（" + _review_failure_reason(review_value) + "），本轮使用规则模板"
        ]
    if any(check.result != "pass" for check in checks.values()):
        return None, [
            "微课堂独立复核未通过（" + _review_failure_reason(review_value) + "），本轮使用规则模板"
        ]

    cited, citation_limitations = attach_verifiable_citations(
        draft, evidence_refs=evidence_refs, lesson_title=title
    )
    limitations = list(citation_limitations)
    if basis == "generic_knowledge":
        limitations.append("没有该生的具体错误证据，本次微课堂为通用知识讲解")
    elif basis == "student_diagnosis":
        limitations.append(
            "只有该生的薄弱表现或错因类型，没有具体错步；本次按诊断范围讲解，未编造具体错误"
        )
    return cited, limitations

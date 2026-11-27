"""C 的模型协作：个性化微课堂生成与独立复核（04 §5、§6）。

三个质量底线：

* **个性化依据**：输入带 ``student_error_context``——该生这次的具体错误、错因和
  教师说明。有错误证据时必须围绕这一步讲；没有证据时明确标为通用知识讲解。
* **可核验引用**：``citation`` 标签由程序从输入 ``EvidenceRef`` 生成，只写知识点
  编号/版本、诊断编号/分析版本或材料页标识；不接受模型自拟页码，不生成外部链接。
* **fail-closed 复核**：复核调用失败、结果不完整或任一项不是 ``pass``，一律退回
  规则模板并记录原因，绝不把模型草稿当正式内容返回。

``pipeline.attach_lessons`` 传入的 ``teaching_points`` 顺序为
``[知识依据定义, 学生引导语, 预期结果, 方案理由, 教师说明]``。第 0 项是知识点
定义，其余项才可能携带具体错误证据；据此派生个性化上下文。未来调用方也可直接
传结构化的 ``student_error_context``。
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

PROMPT_VERSION = "1.1"
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

#: 教学要点里出现这些词，视为带具体错误证据，而不是知识点定义复述。
ERROR_MARKERS: tuple[str, ...] = (
    "错误",
    "错因",
    "做错",
    "写错",
    "出错",
    "错题",
    "纠错",
    "漏乘",
    "漏了",
    "扣分",
    "失分",
    "混淆",
)

GENERIC_REQUIREMENT = (
    "输入没有该生的具体错误证据，本次是通用知识讲解；正文第一段必须明确写出"
    "「这次没有你在这一点的具体错误记录，先按通用讲解」，不得声称针对个人错因。"
)
PERSONALIZED_REQUIREMENT = (
    "正文第一段必须具体复述该生这次的错误表现和错因，并说明本课针对哪一步；"
    "不得只复述知识点定义，也不得把不确定的错因说得比输入更严重。"
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


def _looks_like_error_note(text: str) -> bool:
    return any(marker in text for marker in ERROR_MARKERS)


def build_student_error_context(
    *,
    teaching_points: Sequence[str],
    student_error: str | None = None,
    error_cause: str | None = None,
    teacher_reason: str | None = None,
    evidence_refs: Sequence[EvidenceRef] = (),
    knowledge_definition: str | None = None,
) -> dict[str, Any]:
    """构造个性化依据。没有具体错误证据时返回通用讲解标记，不冒充个性化。"""

    notes = [_clean(item) for item in teaching_points if _clean(item)]
    definition = _clean(knowledge_definition)
    if definition:
        candidates = [note for note in notes if note != definition]
    elif notes and not _looks_like_error_note(notes[0]):
        definition = notes[0]
        candidates = notes[1:]
    else:
        candidates = list(notes)
    observed = [note for note in candidates if _looks_like_error_note(note)][:4]
    teacher_note = _clean(teacher_reason) or next(
        (note for note in candidates if "依据" in note or "教师" in note), ""
    )
    has_error_evidence = any(
        ref.kind in {"finding", "attempt", "grade", "rubric_point"} for ref in evidence_refs
    )
    personalized = bool(
        _clean(student_error) or _clean(error_cause) or observed or has_error_evidence
    )
    return {
        "personalized": personalized,
        "basis": "student_error" if personalized else "generic_knowledge",
        "knowledge_definition": definition,
        "student_error": _clean(student_error),
        "error_cause": _clean(error_cause),
        "teacher_reason": teacher_note,
        "observed_error_notes": observed,
        "requirement": PERSONALIZED_REQUIREMENT if personalized else GENERIC_REQUIREMENT,
    }


def _normalize_error_context(
    context: dict[str, Any] | None,
    *,
    teaching_points,
    evidence_refs,
    knowledge_definition: str = "",
) -> dict[str, Any]:
    merged = build_student_error_context(
        teaching_points=teaching_points,
        evidence_refs=evidence_refs,
        knowledge_definition=knowledge_definition,
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
    if context.get("personalized") is True:
        merged["personalized"] = True
        merged["basis"] = "student_error"
        merged["requirement"] = PERSONALIZED_REQUIREMENT
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
) -> tuple[LessonDraft | None, list[str]]:
    """返回 (草稿, 限制说明)。失败时草稿为 None，由调用方退回规则模板。"""

    error_context = _normalize_error_context(
        student_error_context,
        teaching_points=teaching_points,
        evidence_refs=evidence_refs,
        knowledge_definition=goal,
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

    review_request = ModelTaskRequest(
        task_key="c.review_lesson.v1",
        prompt_version=PROMPT_VERSION,
        namespace="c",
        input_payload={
            "draft": draft.model_dump(mode="json"),
            "knowledge_point_id": kp_id,
            "student_error_context": error_context,
            "evidence_labels": [label.model_dump(mode="json") for label in labels],
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
    if not error_context.get("personalized"):
        limitations.append("没有该生的具体错误证据，本次微课堂为通用知识讲解")
    return cited, limitations

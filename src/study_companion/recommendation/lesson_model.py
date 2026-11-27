"""C 的模型协作：个性化微课堂生成、八项独立复核与一次最小修复（04 §5、§6）。

四个质量底线：

* **个性化依据**：输入带 ``student_error_context``——该生这次的具体错误、错因、
  教师说明和本轮材料编号。区分 ``student_error``（具体错步）、
  ``student_diagnosis``（只有表现/错因类型）和 ``generic_knowledge``（通用讲解）；
  后两种不得写成个人错因。
* **可核验引用**：``citation`` 标签由程序从输入 ``EvidenceRef`` 生成，只写知识点
  编号/版本、诊断编号/分析版本或材料页标识；不接受模型自拟页码，不生成外部链接。
* **逐项复核**：程序下发八项 ``check_id`` 与本次微课堂的 ``object_key``，
  复核结果必须一一对应；缺项、重复、超出下发对象或引用未下发依据都判失败，
  只给总体 ``pass`` 不算通过。
* **fail-closed 修复**：复核返回 ``repair`` 或发现问题时，只调用一次模型按
  ``issues`` 做最小修复；只允许改问题指向的字段/组件，改完重新跑规则硬检和
  独立复核。修复不通过就退回规则模板并记录限制，绝不 fail-open。

``pipeline.attach_lessons`` 传入的 ``teaching_points`` 顺序为
``[知识依据定义, 学生引导语, 预期结果, 方案理由, 教师说明]``。第 0 项是知识点
定义，其余项才可能携带具体错误证据；规划器会把 B 的表现和错因类型写进方案理由，
供这里区分诊断层级。未来调用方也可直接传结构化的 ``student_error_context``、
``material_ids``、``student_id`` 和 ``object_key``。
"""

from __future__ import annotations

import re
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

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
    validate_lesson,
)

PROMPT_VERSION = "1.3"
MAX_NOTE_CHARS = 300
MAX_CITATION_LABELS = 6
DEFAULT_LESSON_OBJECT_KEY = "lesson"

#: 独立复核必须逐项覆盖的检查（04 §5.2 微课堂八项）。
REQUIRED_REVIEW_CHECKS: tuple[str, ...] = (
    "lesson_math",
    "lesson_steps",
    "lesson_personalization",
    "lesson_sources",
    "lesson_schema",
    "lesson_answer_guard",
    "lesson_order",
    "lesson_diagnostic_isolation",
)

#: repair 只允许替换的微课堂字段；其余字段（kp_ids、evidence_refs、source_refs、
#: 组件数量与类型）由程序冻结，模型无法通过 repair 输出改动。
REPAIRABLE_SCALAR_FIELDS: tuple[str, ...] = ("title", "goal", "fallback_text")
SCALAR_LIMITS = {"title": 80, "goal": 200, "fallback_text": 500}

#: 受限中间表达式的允许字符（04 §6.3 MathString）：数字、x/y/a/b、括号与运算符。
_ALLOWED_MATH_CHARS = re.compile(r"^[0-9xyab()+\-*/^=<>.\s]+$")
_RELATION = re.compile(r"<=|>=|(?<![<>=])=(?!=)|<(?![=])|>(?!=)")

_COMPONENT_ADAPTER: TypeAdapter[LessonComponent] = TypeAdapter(LessonComponent)


class LessonReviewIssueOut(BaseModel):
    """复核问题：指向可修复对象的最小定位，repair 只能按这些定位动手。"""

    code: str = Field(min_length=1, max_length=64)
    object_key: str = Field(default="", max_length=64)
    field: str = Field(default="", max_length=32)
    expected: str = Field(default="", max_length=300)


class LessonReviewCheckOut(BaseModel):
    check_id: str = Field(min_length=1, max_length=64)
    #: 兼容历史复核输出：单对象微课堂缺省时按唯一对象补齐，见 ``_review_structure_problems``。
    object_key: str = Field(default="", max_length=64)
    result: Literal["pass", "fail", "uncertain"]
    reason: str = Field(default="", max_length=300)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)


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
    #: 字符串项兼容旧输出；结构化项才带 object_key/field，可作为 repair 的允许范围。
    issues: list[LessonReviewIssueOut | str] = Field(default_factory=list, max_length=8)


class LessonReplacementOut(BaseModel):
    """repair 的单条替换；field 枚举由 schema 收窄，程序再核对对象与范围。"""

    object_key: str = Field(min_length=1, max_length=64)
    field: Literal["title", "goal", "fallback_text", "component"]
    value: Any


class LessonRepairOut(BaseModel):
    status: Literal["ok", "uncertain"]
    replacements: list[LessonReplacementOut] = Field(default_factory=list, max_length=12)
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


def _issue_text(issue: LessonReviewIssueOut | str) -> str:
    if isinstance(issue, str):
        return issue
    where = issue.object_key or "未标对象"
    field = f".{issue.field}" if issue.field else ""
    expected = f"→{issue.expected[:40]}" if issue.expected else ""
    return f"{issue.code}@{where}{field}{expected}"


def _review_failure_reason(
    review: LessonReviewOut,
    extra_problems: Sequence[str] = (),
) -> str:
    """把复核失败压缩成可记录的限制说明；含本地结构问题和逐项结果。"""

    checks = {check.check_id: check for check in review.checks}
    missing = [check_id for check_id in REQUIRED_REVIEW_CHECKS if check_id not in checks]
    failing = [
        f"{check.check_id}:{check.result}"
        + (f"（{check.reason[:60]}）" if check.reason else "")
        for check in review.checks
        if check.result != "pass"
    ]
    parts: list[str] = list(dict.fromkeys(str(item) for item in extra_problems if item))
    if missing:
        parts.append("缺少检查项 " + "、".join(missing))
    if failing:
        parts.append("未通过 " + "、".join(failing))
    if not parts and review.decision != "pass":
        parts.append(f"结论 {review.decision}")
    extra = list(review.reasons) + [_issue_text(item) for item in review.issues]
    if extra:
        parts.append("；".join(str(item) for item in extra)[:160])
    return "；".join(parts)[:300]


def _expected_review_pairs(object_key: str) -> list[tuple[str, str]]:
    return [(check_id, object_key) for check_id in REQUIRED_REVIEW_CHECKS]


def _review_structure_problems(
    review: LessonReviewOut,
    *,
    expected_pairs: Sequence[tuple[str, str]],
    authorized_evidence: set[str],
) -> list[str]:
    """逐项核对 check_id/object_key 与证据范围；返回结构化问题，空列表才合流。"""

    problems: list[str] = []
    allowed_objects: dict[str, set[str]] = {}
    for check_id, object_key in expected_pairs:
        allowed_objects.setdefault(check_id, set()).add(object_key)
    expected = set(expected_pairs)
    seen: set[tuple[str, str]] = set()
    for position, check in enumerate(review.checks, start=1):
        check_id = (check.check_id or "").strip()
        object_key = (check.object_key or "").strip()
        if check_id not in allowed_objects:
            problems.append(f"第 {position} 项出现未下发的检查项 {check_id or '空'}")
            continue
        if not object_key:
            # 历史兼容：单对象微课堂唯一可对应时按下发对象补齐；多对象必须显式给出。
            candidates = allowed_objects[check_id]
            if len(candidates) == 1:
                object_key = next(iter(candidates))
        pair = (check_id, object_key)
        if pair not in expected:
            problems.append(f"检查项 {check_id} 的对象 {object_key or '未标'} 与下发对象不匹配")
            continue
        if pair in seen:
            problems.append(f"检查项 {check_id}（对象 {object_key}）重复返回")
            continue
        seen.add(pair)
        for ref in check.evidence_refs:
            if ref.key() not in authorized_evidence:
                problems.append(
                    f"检查项 {check_id} 引用了未下发的依据 {ref.kind}:{ref.id}"
                )
    missing = [pair for pair in expected_pairs if pair not in seen]
    if missing:
        problems.append("缺少检查项 " + "、".join(check_id for check_id, _ in missing))
    return problems


def _review_has_problems(review: LessonReviewOut) -> bool:
    return (
        review.decision != "pass"
        or any(check.result != "pass" for check in review.checks)
        or bool(review.issues)
    )


def _review_all_pass(review: LessonReviewOut) -> bool:
    return (
        review.decision == "pass"
        and all(check.result == "pass" for check in review.checks)
        and not review.issues
    )


def _basis_conflict(draft: LessonDraft, basis: Any) -> str:
    """检查草稿的依据层级是否与输入一致；返回空串表示通过。"""

    if basis == "generic_knowledge" and not is_generic_lesson(draft):
        return "模型草稿没有具体错误证据却写成个人错因，已退回规则模板"
    if basis == "student_diagnosis" and not (
        is_diagnosis_only_lesson(draft) or is_generic_lesson(draft)
    ):
        return "模型草稿只有诊断表现却写成具体错步，已退回规则模板"
    return ""


def _review_request(
    *,
    draft: LessonDraft,
    lesson_object_key: str,
    kp_id: str,
    error_context: dict[str, Any],
    labels: Sequence[CitationLabel],
    protected_texts: Sequence[str],
    evidence_refs: Sequence[EvidenceRef],
) -> ModelTaskRequest:
    return ModelTaskRequest(
        task_key="c.review_lesson.v1",
        prompt_version=PROMPT_VERSION,
        namespace="c",
        input_payload={
            "draft": draft.model_dump(mode="json"),
            "lesson_object_key": lesson_object_key,
            "knowledge_point_id": kp_id,
            "student_error_context": error_context,
            "evidence_labels": [label.model_dump(mode="json") for label in labels],
            "authorized_evidence_refs": [
                ref.model_dump(mode="json") for ref in evidence_refs
            ],
            "material_ids": list(error_context.get("material_ids") or [])[:8],
            "required_checks": [
                {"check_id": check_id, "object_key": lesson_object_key}
                for check_id in REQUIRED_REVIEW_CHECKS
            ],
            "protected_material_contents": [_clean(item) for item in protected_texts][:12],
        },
        allow_external=False,
    )


def _call_review(
    *,
    service: ModelService,
    draft: LessonDraft,
    lesson_object_key: str,
    kp_id: str,
    error_context: dict[str, Any],
    labels: Sequence[CitationLabel],
    protected_texts: Sequence[str],
    evidence_refs: Sequence[EvidenceRef],
) -> tuple[LessonReviewOut | None, str]:
    """调用独立复核；调用异常与结构错误都返回 (None, 限制说明)，由调用方回退。"""

    request = _review_request(
        draft=draft,
        lesson_object_key=lesson_object_key,
        kp_id=kp_id,
        error_context=error_context,
        labels=labels,
        protected_texts=protected_texts,
        evidence_refs=evidence_refs,
    )
    try:
        result = service.generate_structured(request, LessonReviewOut)
    except Exception as exc:  # 模型服务异常同样按失败处理
        return None, f"微课堂独立复核调用异常（{type(exc).__name__}），本轮改用规则模板"
    if not result.succeeded or result.value is None or not isinstance(result.value, LessonReviewOut):
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return None, f"微课堂独立复核未完成（{code}），本轮改用规则模板"
    return result.value, ""


def _repair_scope(
    *,
    review: LessonReviewOut,
    draft: LessonDraft,
    lesson_object_key: str,
) -> set[tuple[str, str]]:
    """从复核 issues 推出可修复范围；字符串问题没有定位，不能作为修复许可。"""

    component_ids = {component.id for component in draft.components}
    scope: set[tuple[str, str]] = set()
    for issue in review.issues:
        if isinstance(issue, str):
            continue
        object_key = (issue.object_key or "").strip()
        if object_key in component_ids:
            # 组件问题：允许整体替换该组件，但类型与编号仍由程序冻结。
            scope.add((object_key, "component"))
        elif object_key == lesson_object_key and issue.field in REPAIRABLE_SCALAR_FIELDS:
            scope.add((object_key, issue.field))
    return scope


def _call_repair(
    *,
    service: ModelService,
    draft: LessonDraft,
    review: LessonReviewOut,
    lesson_object_key: str,
    kp_id: str,
    error_context: dict[str, Any],
    protected_texts: Sequence[str],
    evidence_refs: Sequence[EvidenceRef],
    scope: set[tuple[str, str]],
) -> tuple[LessonRepairOut | None, list[str]]:
    """调用一次最小修复；只把冻结草稿、问题集合和允许范围交给模型。"""

    request = ModelTaskRequest(
        task_key="c.repair_lesson.v1",
        prompt_version=PROMPT_VERSION,
        namespace="c",
        input_payload={
            "draft": draft.model_dump(mode="json"),
            "lesson_object_key": lesson_object_key,
            "knowledge_point_id": kp_id,
            "student_error_context": error_context,
            "review": review.model_dump(mode="json"),
            "repair_scope": [
                {"object_key": object_key, "field": field}
                for object_key, field in sorted(scope)
            ],
            "allowed_component_types": sorted(
                {component.type for component in draft.components}
            ),
            "authorized_evidence_refs": [
                ref.model_dump(mode="json") for ref in evidence_refs
            ],
            "protected_material_contents": [_clean(item) for item in protected_texts][:12],
        },
        allow_external=False,
    )
    try:
        result = service.generate_structured(request, LessonRepairOut)
    except Exception as exc:
        return None, [f"修复调用异常（{type(exc).__name__}）"]
    if not result.succeeded or result.value is None or not isinstance(result.value, LessonRepairOut):
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return None, [f"修复未完成（{code}）"]
    return result.value, []


def _apply_repair(
    *,
    draft: LessonDraft,
    repair: LessonRepairOut,
    scope: set[tuple[str, str]],
) -> tuple[LessonDraft | None, list[str]]:
    """落实最小修复；越界字段、组件类型/编号变化、非法结构一律整体拒绝。"""

    if repair.status != "ok":
        return None, ["模型未能给出可用的最小修复（status=uncertain）"]
    if not repair.replacements:
        return None, ["修复结果没有给出任何替换"]
    updates: dict[str, Any] = {}
    components = list(draft.components)
    replaced: set[tuple[str, str]] = set()
    for replacement in repair.replacements:
        target = (replacement.object_key, replacement.field)
        if target not in scope:
            return None, [
                f"修复越界：{replacement.object_key}.{replacement.field} 不在复核问题指向的范围内"
            ]
        if target in replaced:
            return None, [f"修复重复替换同一对象：{replacement.object_key}.{replacement.field}"]
        replaced.add(target)
        if replacement.field in REPAIRABLE_SCALAR_FIELDS:
            if not isinstance(replacement.value, str):
                return None, [f"修复字段 {replacement.field} 必须是字符串"]
            value = replacement.value.strip()
            limit = SCALAR_LIMITS[replacement.field]
            if not value or len(value) > limit:
                return None, [f"修复字段 {replacement.field} 为空或超过 {limit} 字"]
            updates[replacement.field] = value
            continue
        index = next(
            (position for position, component in enumerate(components)
             if component.id == replacement.object_key),
            None,
        )
        if index is None:
            return None, [f"修复指向不存在的组件 {replacement.object_key}"]
        try:
            new_component = _COMPONENT_ADAPTER.validate_python(replacement.value)
        except ValidationError:
            return None, [f"修复后的组件 {replacement.object_key} 不符合组件 schema"]
        original = components[index]
        if new_component.type != original.type:
            return None, [
                f"修复擅自改变组件类型：{replacement.object_key} 从 {original.type} 变为 {new_component.type}"
            ]
        if new_component.id != original.id:
            return None, [f"修复擅自改变组件编号：{original.id} 变为 {new_component.id}"]
        components[index] = new_component
    try:
        repaired = LessonDraft.model_validate(
            {
                **draft.model_dump(mode="json"),
                **updates,
                "components": [component.model_dump(mode="json") for component in components],
            }
        )
    except ValidationError:
        return None, ["修复后的微课堂不符合 LessonDraft schema"]
    if repaired.kp_ids != draft.kp_ids or repaired.evidence_refs != draft.evidence_refs:
        return None, ["修复改动了知识点或证据范围"]
    if repaired.source_refs != draft.source_refs:
        return None, ["修复改动了材料来源范围"]
    return repaired, []


def _recheck_repaired(
    repaired: LessonDraft,
    *,
    protected_texts: Sequence[str],
    basis: Any,
) -> list[str]:
    """修复后的全量规则硬检：数学、组件结构与答案保护、个性化依据层级。"""

    problems = list(draft_math_errors(repaired))
    hard_issues = [
        issue for issue in validate_lesson(repaired, protected_texts=list(protected_texts))
        if issue.severity == "error"
    ]
    problems.extend(f"{issue.code}:{issue.message}" for issue in hard_issues)
    basis_problem = _basis_conflict(repaired, basis)
    if basis_problem:
        problems.append(basis_problem)
    return problems


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
    object_key: str = DEFAULT_LESSON_OBJECT_KEY,
) -> tuple[LessonDraft | None, list[str]]:
    """返回 (草稿, 限制说明)。失败时草稿为 None，由调用方退回规则模板。

    ``student_error_context`` 是模型的主要个性化依据；没有具体错误时按
    ``student_diagnosis`` 或 ``generic_knowledge`` 明确记录，不接受模型把通用
    讲解写成个人错因。``object_key`` 是本次微课堂在下发复核检查时使用的对象键；
    复核结果必须与八项检查逐项对应，repair 也只能在该对象和问题指向的字段上动手。
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
    lesson_object_key = _clean(object_key, 64) or DEFAULT_LESSON_OBJECT_KEY
    authorized_evidence = {ref.key() for ref in evidence_refs}
    expected_pairs = _expected_review_pairs(lesson_object_key)

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
    basis_problem = _basis_conflict(draft, basis)
    if basis_problem:
        return None, [basis_problem]

    review, review_failure = _call_review(
        service=service,
        draft=draft,
        lesson_object_key=lesson_object_key,
        kp_id=kp_id,
        error_context=error_context,
        labels=labels,
        protected_texts=protected_texts,
        evidence_refs=evidence_refs,
    )
    if review is None:
        return None, [review_failure]

    structure_problems = _review_structure_problems(
        review,
        expected_pairs=expected_pairs,
        authorized_evidence=authorized_evidence,
    )
    # 明确 reject/uncertain 的复核本身就是失败结论，直接按“未通过”记录；
    # pass/repair 却缺项、重复或引用未下发依据时按“结果不完整”拒绝。
    if structure_problems and review.decision not in {"reject", "uncertain"}:
        return None, [
            "微课堂独立复核结果不完整（"
            + _review_failure_reason(review, structure_problems)
            + "），本轮使用规则模板"
        ]

    limitations: list[str] = []
    if _review_has_problems(review):
        first_failure = _review_failure_reason(review)
        if review.decision not in {"pass", "repair"}:
            return None, [f"微课堂独立复核未通过（{first_failure}），本轮使用规则模板"]
        scope = _repair_scope(
            review=review, draft=draft, lesson_object_key=lesson_object_key
        )
        if not scope:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；复核没有给出可定位的字段或组件，无法做最小修复，本轮使用规则模板"
            ]
        repair, repair_failure = _call_repair(
            service=service,
            draft=draft,
            review=review,
            lesson_object_key=lesson_object_key,
            kp_id=kp_id,
            error_context=error_context,
            protected_texts=protected_texts,
            evidence_refs=evidence_refs,
            scope=scope,
        )
        if repair is None:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；"
                + "；".join(repair_failure)
                + "，本轮使用规则模板"
            ]
        repaired, repair_problems = _apply_repair(
            draft=draft,
            repair=repair,
            scope=scope,
        )
        if repaired is None:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；"
                + "；".join(repair_problems)
                + "，本轮使用规则模板"
            ]
        # 修复后必须全量重检：规则硬检 + 独立复核，不能只信修复结果。
        recheck_problems = _recheck_repaired(
            repaired, protected_texts=protected_texts, basis=basis
        )
        if recheck_problems:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；修复后规则硬检未通过（"
                + "；".join(recheck_problems)[:200]
                + "），本轮使用规则模板"
            ]
        second_review, second_failure = _call_review(
            service=service,
            draft=repaired,
            lesson_object_key=lesson_object_key,
            kp_id=kp_id,
            error_context=error_context,
            labels=labels,
            protected_texts=protected_texts,
            evidence_refs=evidence_refs,
        )
        if second_review is None:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；修复后复审未完成："
                + second_failure
                + "，本轮使用规则模板"
            ]
        second_structure = _review_structure_problems(
            second_review,
            expected_pairs=expected_pairs,
            authorized_evidence=authorized_evidence,
        )
        if second_structure:
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；修复后复审结果不完整（"
                + _review_failure_reason(second_review, second_structure)
                + "），本轮使用规则模板"
            ]
        if not _review_all_pass(second_review):
            return None, [
                "微课堂独立复核未通过（"
                + first_failure
                + "）；修复后复审仍未通过（"
                + _review_failure_reason(second_review)
                + "），本轮使用规则模板"
            ]
        draft = repaired
        limitations.append(
            "独立复核返回 repair，已按 issues 做一次最小修复，并通过规则硬检与独立复核"
        )

    cited, citation_limitations = attach_verifiable_citations(
        draft, evidence_refs=evidence_refs, lesson_title=title
    )
    limitations.extend(citation_limitations)
    if basis == "generic_knowledge":
        limitations.append("没有该生的具体错误证据，本次微课堂为通用知识讲解")
    elif basis == "student_diagnosis":
        limitations.append(
            "只有该生的薄弱表现或错因类型，没有具体错步；本次按诊断范围讲解，未编造具体错误"
        )
    return cited, limitations

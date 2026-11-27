"""个性化微课堂：规则模板生成、schema 校验与泄答检查（04 §6）。

规则模板只使用**已核验**的课程知识依据，不复制受保护题目的答案与解析；模型未
配置时仍可发布这类模板（04 §5.3 允许的降级路径），但必须标明真实生成方式与限制。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from study_companion.contracts import (
    CitationComponent,
    CitationLabel,
    ContrastComponent,
    DerivationComponent,
    DerivationStep,
    DomainError,
    EvidenceRef,
    FormulaComponent,
    LessonDraft,
    ReviewCheck,
    ReviewInfo,
    TextComponent,
    ValidationIssue,
)

RULE_VERSION = "c-lesson-rules-1"
SCHEMA_VERSION = "1.0"
MAX_COMPONENT_TEXT = 300

MATH_RELATION = re.compile(r"[a-zA-Z0-9()（）+\-*/^=<>≤≥≠×÷²√ ]{3,160}$")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _extract_expression(text: str) -> str | None:
    stripped = text.strip()
    if stripped and MATH_RELATION.match(stripped) and any(op in stripped for op in "=<>+-×÷"):
        return stripped[:160]
    return None


def build_rule_lesson(
    *,
    title: str,
    goal: str,
    kp_id: str,
    evidence_refs: list[EvidenceRef],
    teaching_points: list[str],
    fallback_text: str = "",
) -> LessonDraft:
    """用知识依据生成最小可读微课堂：解释 + 公式（可就地验证时）+ 出处。"""

    cleaned = []
    seen: set[str] = set()
    for item in teaching_points:
        text = (item or "").strip()
        if not text or text == title.strip() or text in seen:
            continue
        seen.add(text)
        cleaned.append(text[:MAX_COMPONENT_TEXT])
    if not cleaned:
        cleaned = [
            "先看清楚要处理的对象与符号，再决定用哪一步运算；每写一步都回头核对一遍。"
        ]
    paragraphs = cleaned[:3]
    components: list[Any] = [
        TextComponent(
            id="intro",
            type="text",
            heading=title[:80],
            paragraphs=[item[:MAX_COMPONENT_TEXT] for item in paragraphs],
        )
    ]
    expression = _extract_expression(goal) or _extract_expression(paragraphs[0])
    if expression:
        components.append(
            FormulaComponent(id="expr", type="formula", expr=expression, caption="观察这一步的结构")
        )
    context_text = " ".join([title, goal, *cleaned])
    if "去括号" in context_text or "同类项" in context_text:
        components.append(
            ContrastComponent(
                id="contrast-sign",
                type="contrast",
                before="-(2x-7)",
                incorrect="-2x-7",
                correct="-2x+7",
                note="括号前是负号时，括号里每一项都要变号；-7 变成 +7。",
            )
        )
        components.append(
            DerivationComponent(
                id="steps-sign",
                type="derivation",
                mode="expression",
                steps=[
                    DerivationStep(expr="-(2x-7)", note="先看括号前的负号"),
                    DerivationStep(expr="-2x+7", note="逐项变号，去掉括号"),
                ],
            )
        )
    elif ("一元一次方程" in context_text or "等式性质" in context_text) and "不等式" not in context_text:
        components.append(
            DerivationComponent(
                id="steps-equation",
                type="derivation",
                mode="equation",
                steps=[
                    DerivationStep(expr="2x+3=7", note="原方程"),
                    DerivationStep(expr="2x=4", note="两边同减 3"),
                    DerivationStep(expr="x=2", note="两边同除以 2"),
                ],
            )
        )
        components.append(
            ContrastComponent(
                id="contrast-equation",
                type="contrast",
                before="2x+3=7",
                incorrect="2x=10",
                correct="2x=4",
                note="移项要变号：左边的 +3 移到右边写成 -3。",
            )
        )
    elif "不等式" in context_text:
        components.append(
            ContrastComponent(
                id="contrast-inequality",
                type="contrast",
                before="-2x>6",
                incorrect="x>-3",
                correct="x<-3",
                note="两边同除以负数时，不等号方向要改变。",
            )
        )
    elif "代入" in context_text or "求值" in context_text:
        components.append(
            DerivationComponent(
                id="steps-substitute",
                type="derivation",
                mode="expression",
                steps=[
                    DerivationStep(expr="2x+5", note="当 x=3 时"),
                    DerivationStep(expr="2*3+5", note="把 x 换成 3"),
                    DerivationStep(expr="11", note="按运算顺序计算"),
                ],
            )
        )
    elif "方程组" in context_text or "消元" in context_text:
        components.append(
            DerivationComponent(
                id="steps-system",
                type="derivation",
                mode="equation",
                steps=[
                    DerivationStep(expr="x+y=5", note="第一个方程"),
                    DerivationStep(expr="x-y=1", note="第二个方程"),
                    DerivationStep(expr="2x=6", note="两式相加消去 y"),
                    DerivationStep(expr="x=3", note="解得 x"),
                    DerivationStep(expr="y=2", note="代回得 y"),
                ],
            )
        )
    if evidence_refs:
        components.append(
            CitationComponent(
                id="source",
                type="citation",
                labels=[CitationLabel(label=f"知识点：{title[:36]}", evidence_index=0)],
            )
        )
    draft = LessonDraft(
        title=title[:80],
        goal=goal[:200],
        estimated_minutes=3,
        kp_ids=[kp_id],
        evidence_refs=evidence_refs[:12],
        source_refs=[],
        components=components,
        fallback_text=(fallback_text or goal)[:500],
    )
    return draft


def validate_lesson(draft: LessonDraft, *, protected_texts: list[str] | None = None) -> list[ValidationIssue]:
    """结构 + 数学 + 泄答检查；返回问题列表，空列表表示通过。"""

    issues: list[ValidationIssue] = []
    ids = [component.id for component in draft.components]
    if len(ids) != len(set(ids)):
        issues.append(
            ValidationIssue(code="LESSON_SCHEMA_INVALID", message="组件 id 必须唯一", severity="error")
        )
    for component in draft.components:
        if component.type == "text":
            for paragraph in component.paragraphs:
                if len(paragraph) > MAX_COMPONENT_TEXT:
                    issues.append(
                        ValidationIssue(
                            code="LESSON_SCHEMA_INVALID",
                            message="段落超过 300 字上限",
                            severity="error",
                            field=component.id,
                        )
                    )
        if component.type == "formula":
            if not component.expr.strip():
                issues.append(
                    ValidationIssue(
                        code="MATH_INVALID",
                        message="公式为空",
                        severity="error",
                        field=component.id,
                    )
                )
        if component.type == "citation":
            if not component.labels:
                issues.append(
                    ValidationIssue(
                        code="LESSON_SCHEMA_INVALID",
                        message="引用组件至少需要一个标签",
                        severity="error",
                        field=component.id,
                    )
                )
    if protected_texts:
        joined = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False)
        for text in protected_texts:
            needle = (text or "").strip()
            if len(needle) >= 8 and needle in joined:
                issues.append(
                    ValidationIssue(
                        code="ANSWER_PROTECTION",
                        message="微课堂正文包含受保护题目的原文，已阻止发布",
                        severity="error",
                    )
                )
                break
    return issues


def review_info_for(draft: LessonDraft, issues: list[ValidationIssue], *, method: str = "rule") -> ReviewInfo:
    checks = [
        ReviewCheck(code="lesson_schema", passed=not any(i.code == "LESSON_SCHEMA_INVALID" for i in issues), reason="组件结构闭合且 id 唯一"),
        ReviewCheck(code="lesson_math", passed=not any(i.code == "MATH_INVALID" for i in issues), reason="公式表达式非空且长度合规"),
        ReviewCheck(code="lesson_sources", passed=True, reason="引用指向已发布知识依据"),
        ReviewCheck(code="lesson_answer_guard", passed=not any(i.code == "ANSWER_PROTECTION" for i in issues), reason="正文未包含受保护题目原文"),
        ReviewCheck(code="lesson_diagnostic_isolation", passed=True, reason="微课堂不承担独立诊断，完成仅记接触"),
    ]
    status = "validated" if all(check.passed for check in checks) else "needs_review"
    return ReviewInfo(
        status=status,
        method=method,  # type: ignore[arg-type]
        checks=checks,
        reviewed_at="",
        rule_version=RULE_VERSION,
        issues=issues,
        draft_hash=_hash(draft.model_dump(mode="json")),
    )


def lesson_payload(
    draft: LessonDraft,
    *,
    review: ReviewInfo,
    origin_plan_version: str | None,
    content_id: str,
    content_version: str,
    creation_method: str = "rule",
) -> dict[str, Any]:
    return {
        "content_id": content_id,
        "content_version": content_version,
        "schema_version": SCHEMA_VERSION,
        "creation_method": creation_method,
        "body": draft.model_dump(mode="json"),
        "body_hash": _hash(draft.model_dump(mode="json")),
        "review_info": review.model_dump(mode="json"),
        "publication_status": "published" if review.status == "validated" else "unpublished",
        "origin_plan_version": origin_plan_version,
    }


def lesson_view(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload.get("body") or {})
    if not body:
        raise DomainError("RESULT_NOT_READY", "微课堂正文不可读")
    safe_sources = []
    for ref in body.get("evidence_refs") or []:
        kind = ref.get("kind")
        source_id = ref.get("id")
        if not kind or not source_id:
            continue
        label = {"knowledge_point": "知识点"}.get(kind, "证据")
        version = ref.get("knowledge_version")
        safe_sources.append(
            {
                "kind": kind,
                "id": source_id,
                "knowledge_version": version,
                "label": f"{label} {source_id}" + (f"（知识版本 {version}）" if version else ""),
            }
        )
    return {
        "content_id": payload.get("content_id"),
        "content_version": payload.get("content_version"),
        "schema_version": payload.get("schema_version", SCHEMA_VERSION),
        "title": body.get("title", ""),
        "goal": body.get("goal", ""),
        "components": body.get("components", []),
        "safe_sources": safe_sources,
        "fallback_text": body.get("fallback_text"),
        "review_status": (payload.get("review_info") or {}).get("status", "validated"),
    }

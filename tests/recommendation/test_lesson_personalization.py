"""规则微课堂的个性化：同一知识点不同学生不共用标题与例题（04 §6）。

覆盖验收发现的问题：旧模板只按知识点名称选一个固定例题，标题就是知识点名，
所有同知识点学生拿到同一份正文。这里用合成上下文检查三层依据、知识点分族和
通用讲解限制。
"""

from __future__ import annotations

from study_companion.contracts import EvidenceRef
from study_companion.recommendation.lesson import (
    GENERIC_NOTICE,
    build_rule_lesson,
    lesson_limitations,
    review_info_for,
    validate_lesson,
)
from study_companion.recommendation.lesson_model import draft_math_errors

KP_SUBSTITUTE = "kp-T01I1"
KP_SIMPLIFY = "kp-T01I2"
KP_EQUATION = "kp-T02I1"
KP_SYSTEM = "kp-T02I2"
KP_INEQUALITY = "kp-T03I1"
KP_INTERSECTION = "kp-T03I2"
KP_MODELING = "kp-T01I3"


def _refs(kp_id: str) -> list[EvidenceRef]:
    return [EvidenceRef(kind="knowledge_point", id=kp_id, knowledge_version="kn-1")]


def _build(kp_id: str, title: str, *, error: str | None = None, points: list[str] | None = None, materials: list[str] | None = None):
    teaching_points = points or [
        f"F-STU-D01-N01-{title}",
        f"选用材料 {'、'.join(materials or ['Q001'])}。",
        "学生具体表现：得分率0.20（1/5）（错因类型：step_error）",
    ]
    context = {"student_error": error} if error else None
    return build_rule_lesson(
        title=title,
        goal=title,
        kp_id=kp_id,
        evidence_refs=_refs(kp_id),
        teaching_points=teaching_points,
        student_error_context=context,
        material_ids=materials or [],
    )


def _example_signature(draft) -> tuple:
    parts: list[str] = []
    for component in draft.components:
        if component.type == "derivation":
            parts.extend(step.expr for step in component.steps)
        elif component.type == "contrast":
            parts.extend([component.before, component.incorrect, component.correct])
        elif component.type == "number_line":
            parts.extend(item.label for item in component.sets)
    return tuple(parts)


def test_same_kp_different_student_error_gets_different_title_and_example() -> None:
    first = _build(
        KP_SUBSTITUTE,
        "表示数量关系并代入求值",
        error="上次代入时把 -2 的平方算成了 -4",
    )
    second = _build(
        KP_SUBSTITUTE,
        "表示数量关系并代入求值",
        error="上次代入后先算加减再算乘方，运算顺序反了",
    )
    assert first.title != second.title
    assert "2x+5" not in first.title and "2x+5" not in second.title
    assert _example_signature(first) != _example_signature(second)
    assert draft_math_errors(first) == [] and draft_math_errors(second) == []


def test_same_kp_different_materials_does_not_reuse_same_example() -> None:
    first = _build(KP_SUBSTITUTE, "表示数量关系并代入求值", materials=["Q001", "Q007"])
    second = _build(KP_SUBSTITUTE, "表示数量关系并代入求值", materials=["Q028", "Q040"])
    assert _example_signature(first) != _example_signature(second)


def test_different_kps_use_their_own_example_family() -> None:
    cases = [
        (KP_SUBSTITUTE, "表示数量关系并代入求值"),
        (KP_SIMPLIFY, "识别同类项、去括号并化简"),
        (KP_EQUATION, "运用等式性质解一元一次方程"),
        (KP_SYSTEM, "用消元法解二元一次方程组"),
        (KP_INEQUALITY, "运用不等式性质求解，负数乘除时正确改变方向"),
        (KP_INTERSECTION, "求两个一元一次不等式解集的交集"),
        (KP_MODELING, "用整式解释具体问题"),
    ]
    drafts = [_build(kp_id, title) for kp_id, title in cases]
    signatures = {_example_signature(draft) for draft in drafts}
    assert len(signatures) == len(cases), "不同知识点不能都落到同一套例题"
    assert all(draft.title != title for draft, (_, title) in zip(drafts, cases))
    assert all(draft_math_errors(draft) == [] for draft in drafts)
    # 交集用数轴组件，和代入求值的表达式推导不能混成同一族。
    types = {draft.components[1].type for draft in drafts if len(draft.components) > 1}
    assert "number_line" in types
    assert "derivation" in types


def test_generic_lesson_marks_limitations_explicitly() -> None:
    draft = _build(
        KP_SUBSTITUTE,
        "表示数量关系并代入求值",
        points=["表示数量关系并代入求值的知识点定义"],
        materials=[],
    )
    text = " ".join(
        " ".join(component.paragraphs) for component in draft.components if component.type == "text"
    )
    assert GENERIC_NOTICE in text
    assert lesson_limitations(draft) == [GENERIC_NOTICE]
    issues = validate_lesson(draft)
    assert any(item.code == "LESSON_GENERIC_BASIS" and item.severity == "info" for item in issues)
    review = review_info_for(draft, issues)
    assert review.status == "validated"
    assert any(check.code == "lesson_personalization" and check.passed for check in review.checks)


def test_rule_template_does_not_turn_planner_boilerplate_into_personalization() -> None:
    draft = build_rule_lesson(
        title="表示数量关系并代入求值",
        goal="表示数量关系并代入求值",
        kp_id=KP_SUBSTITUTE,
        evidence_refs=_refs(KP_SUBSTITUTE),
        teaching_points=[
            "存在具体错误证据，安排纠错；依据 F-STU-D01-N01-表示数量关系并代入求值（证据状态 sufficient）；选用材料 Q001。",
            "围绕「表示数量关系并代入求值」安排本轮学习",
        ],
    )
    text = " ".join(
        " ".join(component.paragraphs) for component in draft.components if component.type == "text"
    )
    assert GENERIC_NOTICE in text
    assert "这次记录里，你在" not in text
    assert lesson_limitations(draft) == [GENERIC_NOTICE]


def test_diagnosis_only_lesson_does_not_claim_concrete_error() -> None:
    draft = _build(
        KP_SIMPLIFY,
        "识别同类项、去括号并化简",
        points=[
            "学生具体表现：得分率0.20（1/5）（错因类型：transfer_difficulty）",
        ],
        materials=["Q003"],
    )
    text = " ".join(
        " ".join(component.paragraphs) for component in draft.components if component.type == "text"
    )
    assert "没有具体错步" in text
    assert lesson_limitations(draft)
    assert "具体错步" in lesson_limitations(draft)[0]


def test_multiple_contexts_do_not_share_one_fixed_template() -> None:
    errors = [
        "上次去括号时漏乘了第二项",
        "上次合并同类项时把系数相加后又改了字母次数",
        "上次代入负数时没有加括号",
        "上次两边同除以负数时没有变号",
        "上次解方程组时消元后没有代回另一个方程",
    ]
    drafts = [_build(KP_SUBSTITUTE, "表示数量关系并代入求值", error=error) for error in errors]
    titles = {draft.title for draft in drafts}
    signatures = {_example_signature(draft) for draft in drafts}
    assert len(titles) >= 4, "不同错误上下文不能都得到同一个标题"
    assert len(signatures) >= 4, "不同学生不能共用同一份固定例题"

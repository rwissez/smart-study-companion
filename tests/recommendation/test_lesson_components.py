"""规则微课堂的组件覆盖（04 §6.3）。"""

from __future__ import annotations

from study_companion.contracts import EvidenceRef
from study_companion.recommendation.lesson import build_rule_lesson, validate_lesson


def _build(title: str, goal: str, points: list[str]):
    return build_rule_lesson(
        title=title,
        goal=goal,
        kp_id="kp-1",
        evidence_refs=[
            EvidenceRef(kind="knowledge_point", id="kp-1", knowledge_version="kn-1")
        ],
        teaching_points=points,
        fallback_text="安全回退文本",
    )


def test_equation_lesson_has_derivation_and_contrast() -> None:
    draft = _build(
        "运用等式性质解一元一次方程",
        "会移项并代回检查",
        ["等式两边同加同减同一个数，等式仍成立。"],
    )
    types = [item.type for item in draft.components]
    assert "derivation" in types
    assert "contrast" in types
    assert "citation" in types
    assert validate_lesson(draft) == []


def test_parentheses_lesson_contrasts_sign_error() -> None:
    draft = _build("识别同类项、去括号并化简", "把括号前的负号分配进去", ["负号要乘到括号里的每一项。"])
    contrast = next(item for item in draft.components if item.type == "contrast")
    assert "-2x-7" == contrast.incorrect
    assert "-2x+7" == contrast.correct
    assert validate_lesson(draft) == []


def test_inequality_lesson_flips_sign() -> None:
    draft = _build("运用不等式性质求解", "两边同除以负数时改变方向", ["不等式两边同乘除负数要变号。"])
    contrast = next(item for item in draft.components if item.type == "contrast")
    assert "x<-3" == contrast.correct
    assert validate_lesson(draft) == []


def test_unknown_topic_still_has_text_and_citation() -> None:
    draft = _build("某个没有专门模板的知识点", "理解概念", ["先读定义，再找一个例子。"])
    types = [item.type for item in draft.components]
    assert "text" in types and "citation" in types
    assert validate_lesson(draft) == []

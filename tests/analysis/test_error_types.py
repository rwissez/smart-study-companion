"""错误类型诊断规则（03 §6.7）。"""

from __future__ import annotations

from study_companion.analysis.error_types import AttemptSignal, classify

KNOWLEDGE = {
    "points": [
        {"kp_id": "kp-a", "indicator_ids": ["I1"]},
        {"kp_id": "kp-b", "indicator_ids": ["I2"]},
    ],
    "relations": [
        {"relation_id": "r-1", "type": "prerequisite", "source_ids": ["kp-a"], "target_id": "kp-b"}
    ],
}


def _signal(**kwargs) -> AttemptSignal:
    defaults = dict(
        material_id="Q1",
        indicator_ids=["I2"],
        score=5,
        max_score=5,
        question_type="choice",
        reference_answer="A",
        final_answer="A",
        variant_tag="v1",
        hint_levels_used=0,
        has_solution_steps=False,
    )
    defaults.update(kwargs)
    return AttemptSignal(**defaults)


def test_foundation_gap_needs_confirmed_prerequisite_weakness() -> None:
    found = classify(
        indicator_id="I2",
        attempts=[_signal()],
        findings_by_indicator={
            "I2": {"performance": "needs_practice", "evidence_status": "sufficient"},
            "I1": {"performance": "needs_practice", "evidence_status": "sufficient"},
        },
        knowledge_payload=KNOWLEDGE,
    )
    assert "foundation_gap" in found


def test_foundation_gap_not_claimed_without_evidence() -> None:
    found = classify(
        indicator_id="I2",
        attempts=[_signal()],
        findings_by_indicator={
            "I2": {"performance": "needs_practice", "evidence_status": "sufficient"},
            "I1": {"performance": "undetermined", "evidence_status": "insufficient"},
        },
        knowledge_payload=KNOWLEDGE,
    )
    assert "foundation_gap" not in found


def test_hint_dependency_requires_two_hinted_correct_attempts() -> None:
    one = classify(
        indicator_id="I2",
        attempts=[_signal()],
        findings_by_indicator={},
    )
    assert "hint_dependency" not in one
    two = classify(
        indicator_id="I2",
        attempts=[
            _signal(hint_levels_used=1),
            _signal(material_id="Q2", variant_tag="v2", hint_levels_used=2),
        ],
        findings_by_indicator={},
    )
    assert "hint_dependency" in two


def test_transfer_difficulty_when_variants_split() -> None:
    found = classify(
        indicator_id="I2",
        attempts=[
            _signal(material_id="Q1", variant_tag="v1", score=5),
            _signal(material_id="Q2", variant_tag="v2", score=2),
        ],
        findings_by_indicator={},
    )
    assert "transfer_difficulty" in found


def test_step_error_only_when_final_matches_reference() -> None:
    found = classify(
        indicator_id="I2",
        attempts=[
            _signal(
                question_type="solution",
                score=3,
                max_score=5,
                final_answer="x=3",
                reference_answer="x=3",
                has_solution_steps=True,
            )
        ],
        findings_by_indicator={},
    )
    assert "step_error" in found
    no_steps = classify(
        indicator_id="I2",
        attempts=[
            _signal(
                question_type="solution",
                score=3,
                max_score=5,
                final_answer="x=3",
                reference_answer="x=3",
                has_solution_steps=False,
            )
        ],
        findings_by_indicator={},
    )
    assert "step_error" not in no_steps


def test_ungraded_attempts_yield_no_labels() -> None:
    found = classify(
        indicator_id="I2",
        attempts=[_signal(score=None)],
        findings_by_indicator={
            "I1": {"performance": "needs_practice", "evidence_status": "sufficient"}
        },
        knowledge_payload=KNOWLEDGE,
    )
    assert found == []

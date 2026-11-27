"""A 映射缺口补充：现有知识点优先、两轮、独立复核（02 §9）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.knowledge.gap_fill import MappingDecision, MappingDecisions, fill_mapping_gaps


@dataclass
class Material:
    material_id: str
    material_type: str = "question"
    content: str = "解不等式 2x>6"
    question_type: str = "solution"
    indicator_ids: list[str] = field(default_factory=lambda: ["T03I1"])
    rubric_points: list = field(default_factory=list)


@dataclass
class Point:
    kp_id: str
    name: str
    definition: str
    indicator_ids: list[str] = field(default_factory=list)


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    configured = True

    def __init__(self, decisions: list[MappingDecision]) -> None:
        self.decisions = decisions
        self.calls = 0

    def generate_structured(self, request, schema):  # noqa: ANN001
        value = MappingDecisions(decisions=self.decisions)
        self.calls += 1
        return Result(True, value)


def test_consistent_mapping_creates_annotation() -> None:
    points = [Point("kp-T03I1", "不等式的解集", "所有解组成的集合", ["T03I1"])]
    decision = MappingDecision(
        material_id="Q1", decision="existing", point_id="kp-T03I1", reason="考查解集"
    )
    annotations, notes, gaps = fill_mapping_gaps(
        service=FakeService([decision]),  # type: ignore[arg-type]
        materials=[Material("Q1")],
        points=points,
        existing_annotations=[],
    )
    assert annotations and annotations[0]["target_ids"] == ["kp-T03I1"]
    assert notes and "1 道题" in notes[0]
    assert gaps == []


def test_mismatched_review_keeps_gap() -> None:
    points = [Point("kp-T03I1", "不等式的解集", "所有解组成的集合", ["T03I1"])]
    first = MappingDecision(material_id="Q1", decision="existing", point_id="kp-T03I1")
    second = MappingDecision(material_id="Q1", decision="existing", point_id="kp-other")
    annotations, notes, gaps = fill_mapping_gaps(
        service=FakeService([first, second]),  # type: ignore[arg-type]
        materials=[Material("Q1")],
        points=points,
        existing_annotations=[],
    )
    assert annotations == []
    assert gaps and gaps[0]["status"] == "unmapped"


def test_needs_new_point_is_recorded_without_creating() -> None:
    points = [Point("kp-T03I1", "不等式的解集", "所有解组成的集合", ["T03I1"])]
    decision = MappingDecision(material_id="Q1", decision="needs_new_point", reason="超出范围")
    annotations, notes, gaps = fill_mapping_gaps(
        service=FakeService([decision]),  # type: ignore[arg-type]
        materials=[Material("Q1")],
        points=points,
        existing_annotations=[],
    )
    assert annotations == []
    assert gaps and gaps[0]["status"] == "needs_review"

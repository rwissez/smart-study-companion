"""A 合并判断：两次独立 same 才合并（02 §6.3）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.knowledge.merge_model import MergeJudgment, MergeJudgments, resolve_merges


@dataclass
class Candidate:
    candidate_id: str
    name: str
    definition: str
    page: int = 3
    chapter_id: str = "ch-1"
    aliases: list[str] = field(default_factory=list)
    indicator_ids: list[str] = field(default_factory=list)


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

    def __init__(self, decisions: list[str]) -> None:
        self.decisions = decisions
        self.calls = 0

    def generate_structured(self, request, schema):  # noqa: ANN001
        decision = self.decisions[self.calls]
        self.calls += 1
        pair = request.input_payload["pairs"][0]
        return Result(
            True,
            MergeJudgments(
                judgments=[
                    MergeJudgment(
                        left_id=pair["left_id"],
                        right_id=pair["right_id"],
                        decision=decision,
                        reason="测试",
                        condition_comparison="条件一致",
                    )
                ]
            ),
        )


def _data():
    candidate = Candidate("c1", "不等式的解集", "所有解组成的集合")
    point = Point("kp-T03I1", "不等式的解集", "一个不等式所有解的集合", ["T03I1"])
    return [candidate], [point]


def test_both_same_merges() -> None:
    candidates, points = _data()
    merges, notes = resolve_merges(
        service=FakeService(["same", "same"]), candidates=candidates, points=points  # type: ignore[arg-type]
    )
    assert merges == {"c1": "kp-T03I1"}
    assert any("并入已有" in item for item in notes)


def test_one_same_one_distinct_keeps_pending() -> None:
    candidates, points = _data()
    merges, notes = resolve_merges(
        service=FakeService(["same", "distinct"]), candidates=candidates, points=points  # type: ignore[arg-type]
    )
    assert merges == {}
    assert any("不一致" in item for item in notes)


def test_no_pairs_skips_model() -> None:
    candidate = Candidate("c1", "不等式的解集", "所有解组成的集合")
    point = Point("kp-1", "完全不同的概念", "几何图形")
    merges, notes = resolve_merges(
        service=FakeService([]), candidates=[candidate], points=[point]  # type: ignore[arg-type]
    )
    assert merges == {}
    assert notes == []

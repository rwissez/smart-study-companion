"""A 关系推断：候选、结构校验、环隔离与独立复核（02 §7）。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.knowledge.relation_model import (
    RelationDecision,
    RelationProposal,
    RelationProposals,
    RelationReview,
    collect_candidates,
    infer_relations,
)

POINTS = [
    {
        "kp_id": "kp-a",
        "name": "等式性质",
        "definition": "等式两边同加同减同一个数仍相等",
        "indicator_ids": ["I1"],
        "topic_ids": ["T1"],
        "source_locators": [],
    },
    {
        "kp_id": "kp-b",
        "name": "一元一次方程",
        "definition": "只含一个未知数且次数为 1 的方程",
        "indicator_ids": ["I1"],
        "topic_ids": ["T1"],
        "source_locators": [],
    },
    {
        "kp_id": "kp-c",
        "name": "方程组",
        "definition": "多个方程联立求解",
        "indicator_ids": ["I2"],
        "topic_ids": ["T2"],
        "source_locators": [],
    },
]


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    configured = True

    def __init__(self, proposals: list[RelationProposal], decisions: list[RelationDecision]) -> None:
        self.proposals = proposals
        self.decisions = decisions
        self.calls: list[str] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        if request.task_key.endswith("relations.v1"):
            return Result(True, RelationProposals(relations=self.proposals))
        return Result(True, RelationReview(decisions=self.decisions))


def test_candidates_rank_by_shared_indicator_and_topic() -> None:
    candidates = collect_candidates(POINTS)
    pairs = {(item.left, item.right) for item in candidates}
    assert ("kp-a", "kp-b") in pairs
    assert len(candidates) >= 2


def test_prerequisite_cycle_is_dropped() -> None:
    service = FakeService(
        proposals=[
            RelationProposal(type="prerequisite", source_ids=["kp-a"], target_id="kp-b", reason="先学等式"),
            RelationProposal(type="prerequisite", source_ids=["kp-b"], target_id="kp-a", reason="循环"),
        ],
        decisions=[RelationDecision(index=0, decision="pass"), RelationDecision(index=1, decision="pass")],
    )
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]
    assert len(payload) == 1
    assert payload[0]["source_ids"] == ["kp-a"]
    assert any("环" in item for item in notes)


def test_review_rejection_removes_relation() -> None:
    service = FakeService(
        proposals=[RelationProposal(type="has_property", source_ids=["kp-b"], target_id="kp-a", reason="方向存疑")],
        decisions=[RelationDecision(index=0, decision="reject", reason="方向不成立")],
    )
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]
    assert payload == []
    assert any("未通过独立复核" in item for item in notes)


def test_invalid_endpoint_and_composed_of_single_source_are_dropped() -> None:
    service = FakeService(
        proposals=[
            RelationProposal(type="prerequisite", source_ids=["missing"], target_id="kp-b", reason="x"),
            RelationProposal(type="composed_of", source_ids=["kp-a"], target_id="kp-b", reason="x"),
        ],
        decisions=[RelationDecision(index=0, decision="pass")],
    )
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]
    assert payload == []
    assert any("端点" in item for item in notes)


def test_model_unconfigured_returns_empty_with_reason() -> None:
    class Unconfigured:
        configured = False

        def generate_structured(self, request, schema):  # noqa: ANN001
            raise AssertionError("不应调用模型")

    payload, notes = infer_relations(service=Unconfigured(), points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]
    assert payload == []
    assert notes

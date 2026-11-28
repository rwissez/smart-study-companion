"""A 关系推断：候选上限、分批、结构校验、环隔离与独立复核（02 §7）。"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass

from study_companion.knowledge.relation_model import (
    MAX_BATCHES,
    MAX_CANDIDATES_PER_BATCH,
    MAX_CANDIDATES_PER_POINT,
    MAX_CANDIDATES_TOTAL,
    MAX_POINTS_PER_BATCH,
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
    """显式提案的假模型：只在第一批返回给定提案，复核按给定决定返回。"""

    configured = True

    def __init__(self, proposals: list[RelationProposal], decisions: list[RelationDecision]) -> None:
        self.proposals = proposals
        self.decisions = decisions
        self.calls: list[str] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        if request.task_key.endswith("relations.v1"):
            if self.calls.count("a.relations.v1") > 1:
                return Result(True, RelationProposals(relations=[]))
            return Result(True, RelationProposals(relations=self.proposals))
        return Result(True, RelationReview(decisions=self.decisions))


class AutoService:
    """按输入候选自动提案的假模型；可模拟分批失败与复核失败。"""

    configured = True

    def __init__(
        self,
        *,
        fail_from_batch: int | None = None,
        review_mode: str = "pass",
        review_fails: bool = False,
        per_batch: int = 3,
        proposal_override: list[RelationProposal] | None = None,
    ) -> None:
        self.fail_from_batch = fail_from_batch
        self.review_mode = review_mode
        self.review_fails = review_fails
        self.per_batch = per_batch
        self.proposal_override = proposal_override
        self.proposal_requests: list[dict] = []
        self.review_requests: list[dict] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        if request.task_key.endswith("relations.v1"):
            self.proposal_requests.append(request.input_payload)
            if self.fail_from_batch is not None and len(self.proposal_requests) >= self.fail_from_batch:
                return Result(False, None, {"code": "MODEL_TIMEOUT"})
            if self.proposal_override is not None:
                relations = self.proposal_override if len(self.proposal_requests) == 1 else []
            else:
                candidates = request.input_payload.get("candidates") or []
                relations = [
                    RelationProposal(
                        type="has_property",
                        source_ids=[item["left"]],
                        target_id=item["right"],
                        reason="自动测试提案",
                    )
                    for item in candidates[: self.per_batch]
                ]
            return Result(True, RelationProposals(relations=relations))
        self.review_requests.append(request.input_payload)
        if self.review_fails:
            return Result(False, None, {"code": "MODEL_TIMEOUT"})
        count = len(request.input_payload.get("relations") or [])
        if self.review_mode == "empty":
            decisions: list[RelationDecision] = []
        elif self.review_mode == "reject":
            decisions = [RelationDecision(index=index, decision="reject", reason="不成立") for index in range(count)]
        else:
            decisions = [RelationDecision(index=index, decision="pass") for index in range(count)]
        return Result(True, RelationReview(decisions=decisions))


def make_points(count: int) -> list[dict]:
    return [
        {
            "kp_id": f"kp-{index:03d}",
            "name": f"概念{index}与不等式性质",
            "definition": f"第 {index} 个知识点的定义，说明它与不等式性质的关系" + "。" * 12,
            "indicator_ids": [f"I{index % 6}"],
            "topic_ids": ["T1"],
            "source_locators": [],
        }
        for index in range(count)
    ]


def test_candidates_rank_by_shared_indicator_and_topic() -> None:
    candidates = collect_candidates(POINTS)
    pairs = {(item.left, item.right) for item in candidates}
    assert ("kp-a", "kp-b") in pairs
    assert len(candidates) >= 2


def test_candidate_pool_is_capped_per_point_and_in_total() -> None:
    points = make_points(40)
    candidates = collect_candidates(points)
    degree: Counter[str] = Counter()
    for item in candidates:
        degree[item.left] += 1
        degree[item.right] += 1

    assert len(candidates) <= MAX_CANDIDATES_TOTAL
    assert len(candidates) < 40 * 39 // 2
    assert max(degree.values()) <= MAX_CANDIDATES_PER_POINT


def test_large_point_set_is_batched_and_prompt_is_bounded() -> None:
    points = make_points(60)
    service = AutoService()
    started = time.monotonic()
    payload, notes = infer_relations(service=service, points=points, knowledge_version="kn-large")  # type: ignore[arg-type]
    elapsed = time.monotonic() - started

    assert payload
    assert elapsed < 5
    assert len(service.proposal_requests) <= MAX_BATCHES
    assert len(service.proposal_requests) < len(points)
    for request in service.proposal_requests:
        assert len(request["candidates"]) <= MAX_CANDIDATES_PER_BATCH
        assert len(request["points"]) <= MAX_POINTS_PER_BATCH
        assert len(json.dumps(request, ensure_ascii=False)) < 9000
        request_ids = {item["kp_id"] for item in request["points"]}
        assert {item["left"] for item in request["candidates"]} <= request_ids
        assert {item["right"] for item in request["candidates"]} <= request_ids
    assert all("时间预算" not in note for note in notes)


def test_no_candidates_gives_readable_limitation() -> None:
    points = [
        {
            "kp_id": "kp-1",
            "name": "甲",
            "definition": "甲",
            "indicator_ids": ["I1"],
            "topic_ids": ["T1"],
            "source_locators": [],
        },
        {
            "kp_id": "kp-2",
            "name": "乙",
            "definition": "乙",
            "indicator_ids": ["I2"],
            "topic_ids": ["T2"],
            "source_locators": [],
        },
        {
            "kp_id": "kp-3",
            "name": "丙",
            "definition": "丙",
            "indicator_ids": ["I3"],
            "topic_ids": ["T3"],
            "source_locators": [],
        },
    ]

    payload, notes = infer_relations(service=AutoService(), points=points, knowledge_version="kn-empty")  # type: ignore[arg-type]

    assert payload == []
    assert any("没有可推断关系的候选点对" in note for note in notes)
    assert any("未猜测" in note for note in notes)


def test_valid_relation_is_kept_with_review_status() -> None:
    service = FakeService(
        proposals=[
            RelationProposal(
                type="has_property",
                source_ids=["kp-a"],
                target_id="kp-b",
                reason="等式性质是一元一次方程变形的基础",
            )
        ],
        decisions=[RelationDecision(index=0, decision="pass")],
    )
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert len(payload) == 1
    assert payload[0]["type"] == "has_property"
    assert payload[0]["source_ids"] == ["kp-a"]
    assert payload[0]["target_id"] == "kp-b"
    assert payload[0]["review_status"] == "validated"
    assert payload[0]["relation_id"] == "rel-has_property-kp-a-kp-b"
    assert all("未通过" not in note for note in notes)


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


def test_missing_review_decision_is_not_pass() -> None:
    service = AutoService(review_mode="empty")
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("未通过独立复核" in item for item in notes)


def test_review_timeout_drops_relations_but_is_reported() -> None:
    service = AutoService(review_fails=True)
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("独立复核未完成" in note for note in notes)
    assert any("未通过独立复核" in note for note in notes)


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


def test_multi_source_prerequisite_is_rejected() -> None:
    service = AutoService(
        proposal_override=[
            RelationProposal(
                type="prerequisite",
                source_ids=["kp-a", "kp-b"],
                target_id="kp-c",
                reason="多写了来源",
            )
        ]
    )
    payload, notes = infer_relations(service=service, points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("只能有一个来源" in note for note in notes)


def test_relation_pair_outside_candidates_is_rejected() -> None:
    points = [
        {
            "kp_id": "kp-a",
            "name": "甲",
            "definition": "甲的定义",
            "indicator_ids": ["I1"],
            "topic_ids": ["T1"],
            "source_locators": [],
        },
        {
            "kp_id": "kp-b",
            "name": "乙",
            "definition": "乙的定义",
            "indicator_ids": ["I1"],
            "topic_ids": ["T1"],
            "source_locators": [],
        },
        {
            "kp_id": "kp-c",
            "name": "丙",
            "definition": "丙的定义",
            "indicator_ids": ["I2"],
            "topic_ids": ["T2"],
            "source_locators": [],
        },
        {
            "kp_id": "kp-d",
            "name": "丁",
            "definition": "丁的定义",
            "indicator_ids": ["I2"],
            "topic_ids": ["T2"],
            "source_locators": [],
        },
    ]
    service = AutoService(
        proposal_override=[
            RelationProposal(type="has_property", source_ids=["kp-a"], target_id="kp-c", reason="跨主题候选外")
        ]
    )
    payload, notes = infer_relations(service=service, points=points, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("候选点对之外" in note for note in notes)


def test_composed_of_with_two_sources_is_kept() -> None:
    points = make_points(4)
    service = AutoService(
        proposal_override=[
            RelationProposal(
                type="composed_of",
                source_ids=[points[0]["kp_id"], points[1]["kp_id"]],
                target_id=points[2]["kp_id"],
                reason="两个来源共同推出结果",
            )
        ]
    )
    payload, _notes = infer_relations(service=service, points=points, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert len(payload) == 1
    assert payload[0]["type"] == "composed_of"
    assert len(payload[0]["source_ids"]) == 2


def test_batch_timeout_keeps_relations_from_completed_batches() -> None:
    points = make_points(40)
    service = AutoService(fail_from_batch=2, per_batch=2)
    payload, notes = infer_relations(service=service, points=points, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload
    assert any("MODEL_TIMEOUT" in note for note in notes)
    assert any("部分完成" in note for note in notes)


def test_all_batches_timeout_gives_readable_limitation() -> None:
    points = make_points(40)
    service = AutoService(fail_from_batch=1)
    payload, notes = infer_relations(service=service, points=points, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("MODEL_TIMEOUT" in note for note in notes)
    assert any("没有完成任何一批" in note for note in notes)


def test_model_proposing_nothing_gives_readable_limitation() -> None:
    points = make_points(12)
    service = AutoService(per_batch=0)
    payload, notes = infer_relations(service=service, points=points, knowledge_version="kn-1")  # type: ignore[arg-type]

    assert payload == []
    assert any("没有提出关系" in note for note in notes)


def test_model_unconfigured_returns_empty_with_reason() -> None:
    class Unconfigured:
        configured = False

        def generate_structured(self, request, schema):  # noqa: ANN001
            raise AssertionError("不应调用模型")

    payload, notes = infer_relations(service=Unconfigured(), points=POINTS, knowledge_version="kn-1")  # type: ignore[arg-type]
    assert payload == []
    assert notes

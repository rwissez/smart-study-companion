"""A 的知识关系模型协作（02 §7）。

程序负责候选、结构校验与环隔离；模型只提出关系，独立复核只判断是否成立。
没有模型或复核不通过时，不生成任何新关系，绝不猜测。
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.model_service import ModelService, ModelTaskRequest

RELATION_TYPES = {"prerequisite", "has_property", "derives_from", "composed_of", "special_case"}
MAX_CANDIDATES_PER_POINT = 8


class RelationProposal(BaseModel):
    type: str
    source_ids: list[str] = Field(min_length=1, max_length=4)
    target_id: str
    basis: Literal["standard", "team_inference"] = "team_inference"
    reason: str = ""
    prerequisite_context: str | None = None


class RelationProposals(BaseModel):
    relations: list[RelationProposal] = Field(default_factory=list, max_length=24)


class RelationDecision(BaseModel):
    index: int = Field(ge=0)
    decision: Literal["pass", "reject"]
    reason: str = ""


class RelationReview(BaseModel):
    decisions: list[RelationDecision] = Field(default_factory=list, max_length=24)


@dataclass
class RelationCandidate:
    left: str
    right: str
    shared_indicators: list[str] = field(default_factory=list)
    name_overlap: float = 0.0
    same_topic: bool = False


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[\s，。；：、（）()【】\[\]]+", "", text or "")
    return {cleaned[i : i + 2] for i in range(max(0, len(cleaned) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collect_candidates(points: list[dict[str, Any]]) -> list[RelationCandidate]:
    """按共享指标、主题与名称字面重合排序，每点最多 16 个候选（双向去重）。"""

    by_id = {point["kp_id"]: point for point in points if point.get("kp_id")}
    scored: list[tuple[float, str, str]] = []
    for left_id, right_id in itertools.combinations(by_id, 2):
        left, right = by_id[left_id], by_id[right_id]
        shared = sorted(set(left.get("indicator_ids") or []) & set(right.get("indicator_ids") or []))
        same_topic = bool(set(left.get("topic_ids") or []) & set(right.get("topic_ids") or []))
        overlap = _jaccard(_tokens(left.get("name", "")), _tokens(right.get("name", "")))
        score = len(shared) * 2 + (1 if same_topic else 0) + overlap
        if score <= 0:
            continue
        scored.append((score, left_id, right_id))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    degree: dict[str, int] = {}
    keep: set[tuple[str, str]] = set()
    for score, left_id, right_id in scored:
        if degree.get(left_id, 0) >= MAX_CANDIDATES_PER_POINT and degree.get(right_id, 0) >= MAX_CANDIDATES_PER_POINT:
            continue
        if degree.get(left_id, 0) >= MAX_CANDIDATES_PER_POINT and degree.get(right_id, 0) >= MAX_CANDIDATES_PER_POINT:
            continue
        keep.add((left_id, right_id))
        degree[left_id] = degree.get(left_id, 0) + 1
        degree[right_id] = degree.get(right_id, 0) + 1
    candidates: list[RelationCandidate] = []
    for left_id, right_id in sorted(keep):
        left, right = by_id[left_id], by_id[right_id]
        candidates.append(
            RelationCandidate(
                left=left_id,
                right=right_id,
                shared_indicators=sorted(
                    set(left.get("indicator_ids") or []) & set(right.get("indicator_ids") or [])
                ),
                name_overlap=round(
                    _jaccard(_tokens(left.get("name", "")), _tokens(right.get("name", ""))), 4
                ),
                same_topic=bool(set(left.get("topic_ids") or []) & set(right.get("topic_ids") or [])),
            )
        )
    return candidates


def _validate(
    proposals: list[RelationProposal], point_ids: set[str]
) -> tuple[list[RelationProposal], list[str]]:
    accepted: list[RelationProposal] = []
    issues: list[str] = []
    signatures: set[tuple[str, tuple[str, ...], str]] = set()
    for proposal in proposals:
        sources = sorted({item for item in proposal.source_ids if item})
        if proposal.type not in RELATION_TYPES:
            issues.append(f"未知关系类型 {proposal.type}，已丢弃")
            continue
        if proposal.target_id not in point_ids or any(item not in point_ids for item in sources):
            issues.append("关系端点不在候选知识点中，已丢弃")
            continue
        if proposal.target_id in sources:
            issues.append("关系出现自环，已丢弃")
            continue
        if proposal.type == "composed_of" and len(sources) < 2:
            issues.append("composed_of 需要至少两个来源，已丢弃")
            continue
        if proposal.type != "composed_of" and len(sources) != 1:
            issues.append(f"{proposal.type} 只能有一个来源，已丢弃")
            continue
        if proposal.basis == "team_inference" and not proposal.reason.strip():
            issues.append("推断关系缺少理由，已丢弃")
            continue
        signature = (proposal.type, tuple(sources), proposal.target_id)
        if signature in signatures:
            continue
        signatures.add(signature)
        accepted.append(proposal.model_copy(update={"source_ids": sources}))
    return accepted, issues


def _drop_prerequisite_cycles(proposals: list[RelationProposal]) -> tuple[list[RelationProposal], list[str]]:
    graph: dict[str, set[str]] = {}
    kept: list[RelationProposal] = []
    issues: list[str] = []
    for proposal in proposals:
        if proposal.type != "prerequisite":
            kept.append(proposal)
            continue
        source = proposal.source_ids[0]
        target = proposal.target_id

        def reachable(start: str, goal: str) -> bool:
            stack = [start]
            seen: set[str] = set()
            while stack:
                node = stack.pop()
                if node == goal:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(graph.get(node, ()))
            return False

        if reachable(target, source):
            issues.append(f"前置关系 {source}→{target} 会形成环，已隔离")
            continue
        graph.setdefault(source, set()).add(target)
        kept.append(proposal)
    return kept, issues


def infer_relations(
    *,
    service: ModelService,
    points: list[dict[str, Any]],
    knowledge_version: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """返回可发布的 relation payload 片段与限制说明；模型不可用返回空。"""

    if not service.configured or len(points) < 2:
        return [], ["模型未配置或知识点不足，未做关系推断"]
    candidates = collect_candidates(points)
    if not candidates:
        return [], ["没有可推断关系的候选点对"]
    point_ids = {point["kp_id"] for point in points}

    proposal_request = ModelTaskRequest(
        task_key="a.relations.v1",
        prompt_version="1.0",
        namespace="a",
        input_payload={
            "knowledge_version": knowledge_version,
            "points": [
                {
                    "kp_id": point["kp_id"],
                    "name": point.get("name", "")[:40],
                    "definition": (point.get("definition") or "")[:120],
                    "topic_ids": point.get("topic_ids") or [],
                    "indicator_ids": point.get("indicator_ids") or [],
                }
                for point in points
            ],
            "candidates": [
                {
                    "left": item.left,
                    "right": item.right,
                    "shared_indicators": item.shared_indicators,
                    "same_topic": item.same_topic,
                    "name_overlap": item.name_overlap,
                }
                for item in candidates
            ],
        },
        allow_external=False,
    )
    result = service.generate_structured(proposal_request, RelationProposals)
    if not result.succeeded or result.value is None:
        code = (result.error or {}).get("code", "MODEL_BAD_RESPONSE")
        return [], [f"关系推断未完成（{code}），本轮不新增关系"]
    accepted, issues = _validate(result.value.relations, point_ids)
    accepted, cycle_issues = _drop_prerequisite_cycles(accepted)
    issues.extend(cycle_issues)
    if not accepted:
        return [], issues + ["没有通过结构校验的关系"]

    review_request = ModelTaskRequest(
        task_key="a.review_relation.v1",
        prompt_version="1.0",
        namespace="a",
        input_payload={
            "points": [
                {
                    "kp_id": point["kp_id"],
                    "name": point.get("name", ""),
                    "definition": (point.get("definition") or "")[:200],
                }
                for point in points
            ],
            "relations": [proposal.model_dump(mode="json") for proposal in accepted],
        },
        allow_external=False,
    )
    review = service.generate_structured(review_request, RelationReview)
    if not review.succeeded or review.value is None:
        return [], issues + ["关系独立复核未完成，本轮不新增关系"]
    passed = {
        item.index for item in review.value.decisions if item.decision == "pass"
    }
    kept = [proposal for index, proposal in enumerate(accepted) if index in passed]
    rejected = len(accepted) - len(kept)
    if rejected:
        issues.append(f"{rejected} 条关系未通过独立复核，已丢弃")

    by_id = {point["kp_id"]: point for point in points}
    payload: list[dict[str, Any]] = []
    for proposal in kept:
        source = by_id[proposal.source_ids[0]]
        target = by_id[proposal.target_id]
        payload.append(
            {
                "relation_id": f"rel-{proposal.type}-{proposal.source_ids[0]}-{proposal.target_id}"[:120],
                "type": proposal.type,
                "source_ids": proposal.source_ids,
                "target_id": proposal.target_id,
                "basis": proposal.basis,
                "note": (proposal.reason or "")[:300],
                "source_locators": list(source.get("source_locators") or [])[:2]
                + list(target.get("source_locators") or [])[:2],
                "review_status": "validated",
            }
        )
    return payload, issues

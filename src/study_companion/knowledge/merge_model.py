"""A 去重与合并的模型判断（02 §6.3）。

程序只负责找候选对、校验引用、执行合并；两次独立判断都为 same 才合并，
判断不一致或不确定时保留候选并记录原因，绝不自动折叠。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.model_service import ModelService, ModelTaskRequest

MAX_PAIRS = 16


class MergeJudgment(BaseModel):
    left_id: str = Field(min_length=1, max_length=80)
    right_id: str = Field(min_length=1, max_length=80)
    decision: Literal["same", "distinct", "uncertain"] = "uncertain"
    reason: str = Field(default="", max_length=200)
    condition_comparison: str = Field(default="", max_length=200)
    source_refs: list[str] = Field(default_factory=list, max_length=6)


class MergeJudgments(BaseModel):
    judgments: list[MergeJudgment] = Field(default_factory=list, max_length=MAX_PAIRS)


@dataclass
class Pair:
    candidate_id: str
    point_id: str
    name_overlap: float
    reason: str


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _tokens(text: str) -> set[str]:
    value = _norm(text)
    return {value[i : i + 2] for i in range(max(0, len(value) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collect_pairs(candidates: list[Any], points: list[Any]) -> list[Pair]:
    pairs: list[Pair] = []
    for candidate in candidates:
        cand_name = _norm(getattr(candidate, "name", ""))
        cand_def = _tokens(getattr(candidate, "definition", ""))
        for point in points:
            point_name = _norm(getattr(point, "name", ""))
            if not cand_name or not point_name:
                continue
            overlap = _jaccard(_tokens(cand_name), _tokens(point_name))
            def_overlap = _jaccard(cand_def, _tokens(getattr(point, "definition", "")))
            same_name = cand_name == point_name
            contains = cand_name in point_name or point_name in cand_name
            if not (same_name or contains or overlap >= 0.5 or def_overlap >= 0.4):
                continue
            pairs.append(
                Pair(
                    candidate_id=getattr(candidate, "candidate_id"),
                    point_id=getattr(point, "kp_id"),
                    name_overlap=max(overlap, def_overlap),
                    reason="同名" if same_name else ("名称包含" if contains else "定义词重合"),
                )
            )
    pairs.sort(key=lambda item: (-item.name_overlap, item.candidate_id, item.point_id))
    return pairs[:MAX_PAIRS]


def _prompt_pairs(pairs: list[Pair], candidates: dict[str, Any], points: dict[str, Any]) -> list[dict]:
    payload = []
    for pair in pairs:
        candidate = candidates[pair.candidate_id]
        point = points[pair.point_id]
        payload.append(
            {
                "left_id": pair.candidate_id,
                "left_name": getattr(candidate, "name", ""),
                "left_definition": getattr(candidate, "definition", ""),
                "left_page": getattr(candidate, "page", None),
                "right_id": pair.point_id,
                "right_name": getattr(point, "name", ""),
                "right_definition": getattr(point, "definition", ""),
                "right_indicators": list(getattr(point, "indicator_ids", []) or []),
                "hint": pair.reason,
            }
        )
    return payload


def resolve_merges(
    *,
    service: ModelService,
    candidates: list[Any],
    points: list[Any],
) -> tuple[dict[str, str], list[str]]:
    """返回 {candidate_id: 正式point_id} 与限制说明。"""

    limitations: list[str] = []
    pairs = collect_pairs(candidates, points)
    if not pairs:
        return {}, []
    candidate_by_id = {getattr(candidate, "candidate_id"): candidate for candidate in candidates}
    point_by_id = {getattr(point, "kp_id"): point for point in points}
    payload = _prompt_pairs(pairs, candidate_by_id, point_by_id)

    first = service.generate_structured(
        ModelTaskRequest(
            task_key="a.merge_points.v1",
            prompt_version="1.0",
            namespace="a",
            input_payload={"pairs": payload},
            allow_external=False,
        ),
        MergeJudgments,
    )
    if not first.succeeded or first.value is None:
        return {}, ["合并判断未完成，教材候选全部保留待审"]

    # 独立复核：交换左右、去掉第一次的理由，只给定义与来源。
    reversed_payload = []
    for item in payload:
        reversed_payload.append(
            {
                "left_id": item["right_id"],
                "left_name": item["right_name"],
                "left_definition": item["right_definition"],
                "left_page": None,
                "right_id": item["left_id"],
                "right_name": item["left_name"],
                "right_definition": item["left_definition"],
                "right_indicators": [],
                "hint": "",
            }
        )
    second = service.generate_structured(
        ModelTaskRequest(
            task_key="a.merge_points.review.v1",
            prompt_version="1.0",
            namespace="a",
            input_payload={"pairs": reversed_payload},
            allow_external=False,
        ),
        MergeJudgments,
    )
    if not second.succeeded or second.value is None:
        return {}, ["合并独立复核未完成，教材候选全部保留待审"]

    first_same = {
        (item.left_id, item.right_id)
        for item in first.value.judgments
        if item.decision == "same"
    }
    second_same = {
        (item.right_id, item.left_id)
        for item in second.value.judgments
        if item.decision == "same"
    }
    valid_pairs = {
        (pair.candidate_id, pair.point_id) for pair in pairs
    }
    merges: dict[str, str] = {}
    for pair in pairs:
        key = (pair.candidate_id, pair.point_id)
        if key not in valid_pairs:
            continue
        if key in first_same and key in second_same:
            merges[pair.candidate_id] = pair.point_id
        elif key in first_same or key in second_same:
            limitations.append(
                f"教材候选 {getattr(candidate_by_id[pair.candidate_id], 'name', '')} "
                "与正式点的合并判断不一致，保留待审"
            )
    if merges:
        limitations.append(f"教材候选合并：{len(merges)} 条并入已有正式知识点")
    return merges, limitations

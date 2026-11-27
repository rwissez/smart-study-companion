"""A 映射缺口补充（02 §9）。

题目对不上任何知识点时，先让模型在已有知识点里找；两轮仍找不到就如实记为缺口，
不凭空创建教材概念。所有自动映射都要再过一次独立复核。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from study_companion.model_service import ModelService, ModelTaskRequest

MAX_ROUNDS = 2
ROUND_LIMITS = (12, 24)


class MappingDecision(BaseModel):
    material_id: str = Field(min_length=1, max_length=80)
    decision: Literal["existing", "needs_new_point", "none", "uncertain"] = "uncertain"
    point_id: str = ""
    reason: str = Field(default="", max_length=200)


class MappingDecisions(BaseModel):
    decisions: list[MappingDecision] = Field(default_factory=list, max_length=24)


def _tokens(text: str) -> set[str]:
    value = re.sub(r"\s+", "", text or "").lower()
    return {value[i : i + 2] for i in range(max(0, len(value) - 1))}


def _candidates(material: Any, points: list[Any], limit: int) -> list[Any]:
    wanted = set(getattr(material, "indicator_ids", []) or [])
    material_tokens = _tokens(getattr(material, "content", "") or "")
    scored: list[tuple[float, Any]] = []
    for point in points:
        shared = wanted & set(getattr(point, "indicator_ids", []) or [])
        overlap = len(material_tokens & _tokens(getattr(point, "definition", "")))
        score = len(shared) * 5 + overlap
        if score <= 0:
            continue
        scored.append((score, point))
    scored.sort(key=lambda item: (-item[0], getattr(item[1], "kp_id", "")))
    return [point for _, point in scored[:limit]]


def _decision_map(value: MappingDecisions) -> dict[str, MappingDecision]:
    return {item.material_id: item for item in value.decisions}


def fill_mapping_gaps(
    *,
    service: ModelService,
    materials: list[Any],
    points: list[Any],
    existing_annotations: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    """返回 (新增 annotation 载荷, 限制说明, 未解决缺口)。"""

    limitations: list[str] = []
    gaps: list[dict[str, str]] = []
    if not service.configured or not materials or not points:
        return [], limitations, gaps

    pending = list(materials)
    accepted: list[dict[str, Any]] = []
    for round_index in range(MAX_ROUNDS):
        if not pending:
            break
        limit = ROUND_LIMITS[min(round_index, len(ROUND_LIMITS) - 1)]
        payload = []
        by_material: dict[str, list[Any]] = {}
        for material in pending[:limit]:
            candidates = _candidates(material, points, limit)
            by_material[material.material_id] = candidates
            payload.append(
                {
                    "material_id": material.material_id,
                    "material_type": material.material_type,
                    "question_type": getattr(material, "question_type", ""),
                    "content": (getattr(material, "content", "") or "")[:500],
                    "indicator_ids": list(getattr(material, "indicator_ids", []) or []),
                    "rubric_points": [
                        getattr(item, "description", str(item))
                        for item in (getattr(material, "rubric_points", []) or [])[:6]
                    ],
                    "candidates": [
                        {
                            "point_id": point.kp_id,
                            "name": point.name,
                            "definition": (point.definition or "")[:160],
                            "indicator_ids": list(point.indicator_ids),
                        }
                        for point in candidates
                    ],
                }
            )
        first = service.generate_structured(
            ModelTaskRequest(
                task_key="a.map_material.v1",
                prompt_version="1.0",
                namespace="a",
                input_payload={"materials": payload, "round": round_index + 1},
                allow_external=False,
            ),
            MappingDecisions,
        )
        if not first.succeeded or first.value is None:
            limitations.append(f"第 {round_index + 1} 轮映射判断未完成，剩余题目记为缺口")
            break
        first_map = _decision_map(first.value)

        review = service.generate_structured(
            ModelTaskRequest(
                task_key="a.review_mapping.v1",
                prompt_version="1.0",
                namespace="a",
                input_payload={
                    "materials": [
                        {
                            "material_id": item["material_id"],
                            "content": item["content"],
                            "indicator_ids": item["indicator_ids"],
                            "candidates": item["candidates"],
                        }
                        for item in payload
                    ]
                },
                allow_external=False,
            ),
            MappingDecisions,
        )
        if not review.succeeded or review.value is None:
            limitations.append("映射独立复核未完成，本轮不新增映射")
            break
        review_map = _decision_map(review.value)

        resolved_ids: set[str] = set()
        still_pending: list[Any] = []
        for material in pending[:limit]:
            decision = first_map.get(material.material_id)
            check = review_map.get(material.material_id)
            if decision is None or check is None:
                still_pending.append(material)
                continue
            if (
                decision.decision == "existing"
                and check.decision == "existing"
                and decision.point_id
                and decision.point_id == check.point_id
                and any(point.kp_id == decision.point_id for point in points)
            ):
                accepted.append(
                    {
                        "annotation_id": f"ann-gap-{material.material_id}",
                        "source_type": "material",
                        "source_id": material.material_id,
                        "target_ids": [decision.point_id],
                        "knowledge_role": "primary",
                        "basis": "model",
                        "evidence": (decision.reason or "模型映射并经独立复核")[:200],
                        "review_info": {
                            "status": "validated",
                            "method": "model",
                            "checks": [
                                {
                                    "code": "MAPPING_INDEPENDENT_REVIEW",
                                    "passed": True,
                                    "reason": "题目映射由独立模型复核一致",
                                }
                            ],
                            "reviewed_at": "",
                            "rule_version": "a-rules-1",
                        },
                    }
                )
                resolved_ids.add(material.material_id)
            elif decision.decision == "needs_new_point" and check.decision == "needs_new_point":
                gaps.append(
                    {
                        "material_id": material.material_id,
                        "status": "needs_review",
                        "reason": "现有知识点均不覆盖，需要回到教材页补提取",
                    }
                )
                resolved_ids.add(material.material_id)
            else:
                still_pending.append(material)
        pending = still_pending + pending[limit:]
        if not still_pending:
            break

    for material in pending:
        gaps.append(
            {
                "material_id": material.material_id,
                "status": "unmapped",
                "reason": "两轮映射仍未得到一致结论",
            }
        )
    if accepted:
        limitations.append(f"映射缺口补充：{len(accepted)} 道题通过独立复核建立知识点关联")
    return accepted, limitations, gaps

"""错误类型诊断（03 §6.7、§10）。

只用程序可核对的事实，不猜"学生为什么错"：

- ``foundation_gap``：知识图谱里该指标的前置指标已被判定需要加强；
- ``hint_dependency``：同一指标上多次借助提示才做对；
- ``transfer_difficulty``：同指标的不同变式里，有的做对、有的做错；
- ``step_error``：结果与参考答案一致但过程分不满（说明丢分在步骤）。

证据不足时返回空列表，不硬贴标签。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ERROR_TYPES = ("foundation_gap", "hint_dependency", "transfer_difficulty", "step_error")


@dataclass
class AttemptSignal:
    material_id: str
    indicator_ids: list[str] = field(default_factory=list)
    score: int | None = None
    max_score: int = 0
    question_type: str | None = None
    reference_answer: str | None = None
    final_answer: str | None = None
    variant_tag: str | None = None
    hint_levels_used: int = 0
    has_solution_steps: bool = False

    @property
    def graded(self) -> bool:
        return self.score is not None

    @property
    def correct(self) -> bool:
        return bool(self.max_score) and self.score == self.max_score


def _normalise(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).strip().lower()


def _prerequisite_pairs(knowledge_payload: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for relation in knowledge_payload.get("relations", []) or []:
        relation_type = str(relation.get("type") or "")
        if "prerequisite" not in relation_type:
            continue
        for source in relation.get("source_ids") or []:
            target = relation.get("target_id")
            if target:
                pairs.append((str(source), str(target)))
    return pairs


def _indicator_of_point(knowledge_payload: dict[str, Any], point_id: str) -> str | None:
    for point in knowledge_payload.get("points", []) or []:
        if point.get("kp_id") == point_id:
            indicators = point.get("indicator_ids") or []
            return str(indicators[0]) if indicators else None
    return None


def classify(
    *,
    indicator_id: str,
    attempts: list[AttemptSignal],
    findings_by_indicator: dict[str, dict[str, Any]],
    knowledge_payload: dict[str, Any] | None = None,
    hint_threshold: int = 2,
) -> list[str]:
    """返回该指标上可被证据支持的错误类型（可能为空）。"""

    found: list[str] = []
    relevant = [item for item in attempts if indicator_id in item.indicator_ids and item.graded]
    if not relevant:
        return found

    # 前置缺口：只看已确认薄弱的前置指标。
    if knowledge_payload:
        for source_point, target_point in _prerequisite_pairs(knowledge_payload):
            if _indicator_of_point(knowledge_payload, target_point) != indicator_id:
                continue
            prereq_indicator = _indicator_of_point(knowledge_payload, source_point)
            prereq = findings_by_indicator.get(prereq_indicator or "")
            if (
                prereq
                and prereq.get("performance") == "needs_practice"
                and prereq.get("evidence_status") == "sufficient"
            ):
                found.append("foundation_gap")
                break

    # 提示依赖：同一指标上至少两次"用了提示才做对"。
    hinted_correct = [item for item in relevant if item.hint_levels_used > 0 and item.correct]
    if len(hinted_correct) >= hint_threshold:
        found.append("hint_dependency")

    # 迁移困难：不同变式间一对一错。
    by_variant: dict[str, list[AttemptSignal]] = {}
    for item in relevant:
        key = item.variant_tag or item.material_id
        by_variant.setdefault(key, []).append(item)
    if len(by_variant) >= 2:
        has_right = any(any(item.correct for item in group) for group in by_variant.values())
        has_wrong = any(
            any(item.score is not None and item.score < item.max_score for item in group)
            for group in by_variant.values()
        )
        if has_right and has_wrong:
            found.append("transfer_difficulty")

    # 步骤失分：结果与参考答案一致，但没有拿满分。
    for item in relevant:
        if item.question_type != "solution" or item.correct:
            continue
        if not item.has_solution_steps or not item.reference_answer:
            continue
        if _normalise(item.final_answer) and _normalise(item.final_answer) == _normalise(item.reference_answer):
            found.append("step_error")
            break

    return [item for item in ERROR_TYPES if item in found]


def signals_from_events(
    events: list[Any], materials: dict[str, Any]
) -> dict[str, list[AttemptSignal]]:
    """把作答事件按指标分组为诊断信号；缺字段不猜。"""

    grouped: dict[str, list[AttemptSignal]] = {}
    for event in events:
        material = materials.get(event.material_id)
        if material is None:
            continue
        payload = getattr(event, "payload", None) or {}
        signal = AttemptSignal(
            material_id=event.material_id,
            indicator_ids=list(material.indicator_ids),
            score=getattr(event, "score", None),
            max_score=getattr(event, "max_score", 0) or 0,
            question_type=material.question_type,
            reference_answer=material.reference_answer,
            final_answer=payload.get("final_answer") if isinstance(payload, dict) else None,
            variant_tag=material.reviewed_variant_tag,
            hint_levels_used=int(payload.get("hint_levels_used") or 0) if isinstance(payload, dict) else 0,
            has_solution_steps=bool(payload.get("solution_steps")) if isinstance(payload, dict) else False,
        )
        for indicator_id in signal.indicator_ids:
            grouped.setdefault(indicator_id, []).append(signal)
    return grouped

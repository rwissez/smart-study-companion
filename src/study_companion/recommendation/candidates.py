"""C 的候选资格与选题评分（04 §4.1—§4.2）。

这一层只回答两个问题：**哪些题可以被选**、**选了以后怎么排序**。

准入是硬约束（范围、节点允许题、正式目录、历史重复、近变式簇上限）；
评分是软排序（K/E/D/H/V/M 六维加权），每一项都保留分值与来源，
教师端可以逐项核对"为什么先做这道题"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from study_companion.contracts import (
    Catalog,
    DifficultyAssessment,
    Material,
)
from study_companion.recommendation import difficulty as difficulty_rules
from study_companion.recommendation import fingerprint as fingerprint_rules

#: 04 §4.2 的各策略权重 [K, E, D, H, V, M]。
STRATEGY_WEIGHTS: dict[str, tuple[float, float, float, float, float, float]] = {
    "collect_evidence": (0.34, 0.05, 0.20, 0.24, 0.03, 0.14),
    "prerequisite_support": (0.32, 0.17, 0.20, 0.15, 0.05, 0.11),
    "error_correction": (0.25, 0.30, 0.15, 0.10, 0.12, 0.08),
    "consolidation": (0.25, 0.08, 0.20, 0.12, 0.18, 0.17),
    "transfer": (0.22, 0.05, 0.14, 0.15, 0.22, 0.22),
}

#: 04 §4.2 的初始题量区间（下限, 上限）。
STRATEGY_VOLUME: dict[str, tuple[int, int]] = {
    "collect_evidence": (2, 3),
    "prerequisite_support": (1, 3),
    "error_correction": (2, 4),
    "consolidation": (3, 5),
    "transfer": (1, 3),
}

#: 各策略期望的难度档（用于 D 分）。
STRATEGY_TARGET_LEVEL: dict[str, str] = {
    "collect_evidence": "basic",
    "prerequisite_support": "basic",
    "error_correction": "intermediate",
    "consolidation": "intermediate",
    "transfer": "advanced",
}

LEVEL_ORDER = {"basic": 0, "intermediate": 1, "advanced": 2}

RETRY_WINDOW_DAYS = 14
SPACED_REVIEW_DAYS = 7
FEEDBACK_NEXT_DAY = 1

QUERY_BUDGET_QUESTIONS = 20
QUERY_BUDGET_SUPPORT = 8


@dataclass
class HistoryItem:
    material_id: str
    origin: str  # official / online
    status: str  # graded / pending / unanswered / in_progress / submitted / completed
    occurred_at: datetime | None
    assistance: str = "unknown"
    attempt_id: str | None = None
    score: int | None = None
    max_score: int | None = None


@dataclass
class LearningHistoryView:
    items: list[HistoryItem] = field(default_factory=list)

    def for_material(self, material_id: str) -> list[HistoryItem]:
        return [item for item in self.items if item.material_id == material_id]

    def ever_completed(self, material_id: str) -> bool:
        return any(
            item.status in {"graded", "completed"} and (item.score is not None)
            for item in self.for_material(material_id)
        )

    def ever_opened(self, material_id: str) -> bool:
        return bool(self.for_material(material_id))

    def last_help_at(self, material_id: str) -> datetime | None:
        helped = [
            item.occurred_at
            for item in self.for_material(material_id)
            if item.assistance == "assisted" and item.occurred_at is not None
        ]
        return max(helped) if helped else None

    def last_completed_at(self, material_id: str) -> datetime | None:
        completed = [
            item.occurred_at
            for item in self.for_material(material_id)
            if item.status in {"graded", "completed"} and item.occurred_at is not None
        ]
        return max(completed) if completed else None

    def retried_within(self, material_id: str, days: int, now: datetime) -> bool:
        window = now - timedelta(days=days)
        return any(
            item.occurred_at is not None and item.occurred_at >= window
            for item in self.for_material(material_id)
        )


@dataclass
class CandidatePool:
    catalog: Catalog
    knowledge_payload: dict[str, Any]
    node_available_ids: list[str] | None
    history: LearningHistoryView
    now: datetime


@dataclass
class ScoredCandidate:
    material: Material
    fingerprint: fingerprint_rules.Fingerprint
    difficulty: DifficultyAssessment | None
    role: str
    estimated_minutes: int
    score: float
    parts: dict[str, float]
    repeat_reason: str | None = None
    limitations: list[str] = field(default_factory=list)

    def as_ref(self) -> dict[str, Any]:
        return {
            "material_id": self.material.material_id,
            "score": self.score,
            "parts": self.parts,
            "role": self.role,
            "difficulty_level": self.difficulty.level if self.difficulty else None,
            "difficulty_status": self.difficulty.status if self.difficulty else "unknown",
            "estimate_minutes": self.estimated_minutes,
            "repeat_reason": self.repeat_reason,
            "limitations": self.limitations,
        }


@dataclass
class SelectionResult:
    selected: list[ScoredCandidate] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    shortage_reason: str | None = None

    @property
    def exhausted(self) -> bool:
        return not self.selected


def _annotation_roles(knowledge_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """material_id → {kp_id: primary|supporting}。"""

    roles: dict[str, dict[str, str]] = {}
    for annotation in knowledge_payload.get("annotations", []):
        if annotation.get("source_type") != "material":
            continue
        material_id = annotation.get("source_id")
        role = annotation.get("knowledge_role") or "primary"
        for target in annotation.get("target_ids", []):
            roles.setdefault(material_id, {})[target] = role
    return roles


def _within_scope(material: Material, need: dict[str, Any]) -> bool:
    indicator_id = need.get("indicator_id")
    return bool(indicator_id) and indicator_id in material.indicator_ids


def _repeat_reason(
    material: Material, history: LearningHistoryView, now: datetime
) -> tuple[str | None, str | None]:
    """判断这道题能否作为"重练"出现，返回 (reason, 排除原因)。"""

    if not history.ever_completed(material.material_id):
        return None, None
    if history.retried_within(material.material_id, RETRY_WINDOW_DAYS, now):
        return None, "RECENTLY_RETRIED"
    last_help = history.last_help_at(material.material_id)
    if last_help is not None and (now - last_help) >= timedelta(days=FEEDBACK_NEXT_DAY):
        return "error_retry", None
    last_completed = history.last_completed_at(material.material_id)
    if last_completed is not None and (now - last_completed) >= timedelta(days=SPACED_REVIEW_DAYS):
        return "spaced_review", None
    return None, "COMPLETED_WITHOUT_RETRY_REASON"


def _history_score(
    material: Material, history: LearningHistoryView, repeat_reason: str | None
) -> float:
    if not history.ever_opened(material.material_id):
        return 1.0
    if not history.ever_completed(material.material_id):
        return 0.65
    if repeat_reason is not None:
        return 0.45
    return 0.0


def _difficulty_score(
    strategy: str, assessment: DifficultyAssessment | None
) -> float:
    if assessment is None:
        return 0.3
    target = LEVEL_ORDER.get(STRATEGY_TARGET_LEVEL.get(strategy, "intermediate"), 1)
    actual = LEVEL_ORDER.get(assessment.level, 1)
    distance = abs(target - actual)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.6
    return 0.0


def _evidence_score(
    strategy: str, material: Material, need: dict[str, Any], finding: dict[str, Any] | None
) -> float:
    if strategy == "error_correction" and finding is not None:
        focus = set(finding.get("rubric_focus") or [])
        if focus and {point.point_id for point in material.rubric_points} & focus:
            return 1.0
        if finding.get("error_types"):
            return 0.5
        return 0.0
    if finding is not None and finding.get("evidence_status") == "sufficient":
        return 0.5
    return 0.5


def score_material(
    material: Material,
    *,
    need: dict[str, Any],
    pool: CandidatePool,
    roles: dict[str, dict[str, str]],
    selected: list[ScoredCandidate],
    strategy: str,
    finding: dict[str, Any] | None,
) -> ScoredCandidate | None:
    """给单道题打分；返回 None 表示被硬规则排除（原因由调用方记录）。"""

    if material.material_type != "question":
        return None
    if material.question_type is None:
        return None
    if not _within_scope(material, need):
        return None
    if pool.node_available_ids is not None and material.material_id not in pool.node_available_ids:
        return None

    repeat_reason, blocked = _repeat_reason(material, pool.history, pool.now)
    if blocked:
        return None

    material_roles = roles.get(material.material_id, {})
    kp_id = need.get("kp_id")
    role = "primary"
    if kp_id and material_roles:
        role = material_roles.get(kp_id, "supporting")
    if role != "primary" and strategy in {"transfer"}:
        role = "supporting"

    assessment = difficulty_rules.assess(material, primary_kp_count=max(1, len(material_roles) or 1))
    fp = fingerprint_rules.build_fingerprint(material, primary_kp_ids=list(material_roles))

    # 同簇上限：已选集合里与它近变的题达到 2 道就排除。
    cluster_size = sum(
        1 for item in selected if fingerprint_rules.is_near_variant(item.fingerprint, fp)
    )
    if cluster_size >= fingerprint_rules.MAX_PER_CLUSTER:
        return None

    k_score = 1.0 if role == "primary" else 0.55
    e_score = _evidence_score(strategy, material, need, finding)
    d_score = _difficulty_score(strategy, assessment)
    h_score = _history_score(material, pool.history, repeat_reason)
    if h_score == 0.0:
        return None

    if not selected:
        v_score = 0.5
        m_score = 1.0
    else:
        best_similarity = max(
            fingerprint_rules.similarity(item.fingerprint, fp) for item in selected
        )
        m_score = round(max(0.0, 1.0 - best_similarity), 6)
        same_role = any(item.role == role for item in selected)
        v_score = 0.4 if same_role else 1.0

    weights = STRATEGY_WEIGHTS.get(strategy, STRATEGY_WEIGHTS["consolidation"])
    score = 100 * (
        weights[0] * k_score
        + weights[1] * e_score
        + weights[2] * d_score
        + weights[3] * h_score
        + weights[4] * v_score
        + weights[5] * m_score
    )

    limitations: list[str] = []
    if assessment is None:
        limitations.append("难度特征抽取不足，按未知难度参与普通练习")
    if pool.node_available_ids is None:
        limitations.append("该节点未登记可用题目清单，按目录范围选取")

    return ScoredCandidate(
        material=material,
        fingerprint=fp,
        difficulty=assessment,
        role=role,
        estimated_minutes=difficulty_rules.estimated_minutes(
            assessment, solution=material.question_type == "solution"
        ),
        score=round(score, 6),
        parts={
            "K": round(k_score, 6),
            "E": round(e_score, 6),
            "D": round(d_score, 6),
            "H": round(h_score, 6),
            "V": round(v_score, 6),
            "M": round(m_score, 6),
        },
        repeat_reason=repeat_reason,
        limitations=limitations,
    )


def select_for_need(
    *,
    need: dict[str, Any],
    pool: CandidatePool,
    strategy: str,
    finding: dict[str, Any] | None,
    remaining_minutes: int,
    max_activities: int,
    config_minutes: int,
    exclude_material_ids: set[str] | None = None,
) -> SelectionResult:
    """按策略卷与负担上限选题。

    贪心构造：每轮重算 M（与已选题的差异），选当前最高分且仍在预算内的题；
    同分按 material_id 升序，保证可重放。
    """

    result = SelectionResult()
    excluded = exclude_material_ids or set()
    roles = _annotation_roles(pool.knowledge_payload)
    candidates: list[ScoredCandidate] = []
    budget_used = 0
    for material in pool.catalog.materials:
        if len(candidates) >= QUERY_BUDGET_QUESTIONS:
            break
        if material.material_id in excluded:
            result.rejected.append({'material_id': material.material_id, 'reason': 'ALREADY_SELECTED_IN_ROUND'})
            continue
        scored = score_material(
            material,
            need=need,
            pool=pool,
            roles=roles,
            selected=[],
            strategy=strategy,
            finding=finding,
        )
        if scored is None:
            result.rejected.append(
                {"material_id": material.material_id, "reason": _rejection_reason(material, need, pool, roles)}
            )
            continue
        candidates.append(scored)

    volume_low, volume_high = STRATEGY_VOLUME.get(strategy, (1, 3))
    target_count = min(volume_high, max(volume_low, 1), max_activities)
    selected: list[ScoredCandidate] = []
    while len(selected) < target_count:
        rescored: list[ScoredCandidate] = []
        for material in pool.catalog.materials:
            if material.material_id in excluded:
                continue
            if any(item.material.material_id == material.material_id for item in selected):
                continue
            if len(rescored) >= QUERY_BUDGET_QUESTIONS:
                break
            scored = score_material(
                material,
                need=need,
                pool=pool,
                roles=roles,
                selected=selected,
                strategy=strategy,
                finding=finding,
            )
            if scored is not None:
                rescored.append(scored)
        if not rescored:
            break
        rescored.sort(key=lambda item: (-item.score, item.material.material_id))
        chosen = None
        for candidate in rescored:
            if budget_used + candidate.estimated_minutes <= min(remaining_minutes, config_minutes):
                chosen = candidate
                break
        if chosen is None:
            result.shortage_reason = "BUDGET_EXHAUSTED"
            break
        selected.append(chosen)
        budget_used += chosen.estimated_minutes

    result.selected = selected
    if not selected:
        result.shortage_reason = result.shortage_reason or "NO_ELIGIBLE_MATERIAL"
    return result


def _rejection_reason(
    material: Material,
    need: dict[str, Any],
    pool: CandidatePool,
    roles: dict[str, dict[str, str]],
) -> str:
    if material.material_type != "question":
        return "NOT_A_QUESTION"
    if material.question_type is None:
        return "QUESTION_TYPE_UNKNOWN"
    if not _within_scope(material, need):
        return "OUT_OF_SCOPE"
    if pool.node_available_ids is not None and material.material_id not in pool.node_available_ids:
        return "NOT_AVAILABLE_IN_NODE"
    _, blocked = _repeat_reason(material, pool.history, pool.now)
    if blocked:
        return blocked
    return "EXCLUDED"

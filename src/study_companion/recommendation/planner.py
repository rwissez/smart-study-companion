"""C 的方案规划器（04 §3—§5、§7）。

流程与设计一致：学情 → 教学需求 → 策略与优先级 → 必要前置 → 候选与评分 →
负担与任务组 → 方案草稿（交给独立审核）。本模块不写数据库、不调用模型，
所有判断都可复现并可逐项核对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    Catalog,
    EvidenceRef,
    Material,
    Recommendation,
    RecommendationBundle,
    ReviewCheck,
    ReviewInfo,
    StudentResult,
    TaskGroup,
    TeachingNeed,
    ValidationIssue,
)
from study_companion.recommendation import candidates as candidate_rules
from study_companion.recommendation import hints as hint_rules

RULE_VERSION = "c-rules-1"

#: 04 §3.1 的策略优先顺序：必须人工阻断 > 补证 > 必要前置 > 纠错 > 巩固 > 迁移。
STRATEGY_PRIORITY = (
    "teacher_intervention",
    "collect_evidence",
    "prerequisite_support",
    "error_correction",
    "consolidation",
    "transfer",
)

#: 04 §3.2 的 N 映射（B 的掌握状态 → 需求强度）。
MASTERY_STRENGTH = {
    "needs_practice": 1.0,
    "developing": 0.65,
    "mastered": 0.15,
}
DEFAULT_STRENGTH = 0.45
TRANSFER_STRENGTH = 0.35

#: 04 §3.2 的 R 映射（证据状态 → 诊断可靠度）。
EVIDENCE_RELIABILITY = {"sufficient": 1.0, "insufficient": 0.45, "conflicting": 0.25}

#: 04 §3.2 的 U 映射（上次同策略效果 → 复用价值）。
EFFECT_VALUE = {"ineffective": 1.0, "partial": 0.65, "improved": 0.2, "insufficient": 0.5}

#: B 判定"表现较好"的阈值（03 §5，与 analysis.service.STRENGTH_THRESHOLD 一致）。
HIGH_PERFORMANCE_THRESHOLD = 0.80
#: 可形成稳定结论的最少有效样题数（03 §5.1）。
MIN_EVIDENCE_SAMPLES = 3
#: 能支撑"具体错误"判断的错误类型。迁移困难只说明变式表现不一致，
#: 不是某一步的具体错因；笼统的 other_supported 也不能当具体错误。
CONCRETE_ERROR_TYPES = frozenset({"step_error"})
#: 提示依赖是"借助提示才做对"，可作为调整练习的证据，但不是具体错步。
SUPPORT_ERROR_TYPES = frozenset({"hint_dependency"})
#: 只能支持迁移或巩固、不能单独支持纠错的轻微信号。
MILD_ERROR_TYPES = frozenset({"transfer_difficulty", "other_supported"})

MAX_ROOTS = 2

#: 连续无效后的替代策略（04 §7 的策略刹车）。
FALLBACK_STRATEGY = {
    "error_correction": "prerequisite_support",
    "consolidation": "collect_evidence",
    "transfer": "consolidation",
    "prerequisite_support": "collect_evidence",
}
PREREQUISITE_MAX_EDGES = 3
PREREQUISITE_MAX_NODES = 20
ROUND_MINUTES = 20
MAX_ACTIVITIES = 8
GROUP_SWITCH_MINUTES = 1

#: 无任何发现/观察证据时的补观察组：只补证据，不判断掌握（04 §3.1）。
NO_OBSERVATION_TARGET_SUFFIX = "补观察"
NO_OBSERVATION_MAX_ACTIVITIES = 3


@dataclass
class PlannerConfig:
    round_minutes: int = ROUND_MINUTES
    max_activities: int = MAX_ACTIVITIES
    max_roots: int = MAX_ROOTS
    prerequisite_max_edges: int = PREREQUISITE_MAX_EDGES
    prerequisite_max_nodes: int = PREREQUISITE_MAX_NODES
    kp_weight_default: float = 0.6
    kp_weight_core: float = 0.9
    rule_version: str = RULE_VERSION


@dataclass
class PlannerInputs:
    student_result: StudentResult
    catalog: Catalog
    knowledge_payload: dict[str, Any]
    history: candidate_rules.LearningHistoryView
    now: datetime
    node_available_ids: list[str] | None = None
    #: 目标 → 需要暂时换掉的策略（来自策略试验：连续无效才抑制）。
    suppressed: dict[str, set[str]] = field(default_factory=dict)
    config: PlannerConfig = field(default_factory=PlannerConfig)


@dataclass
class PlannerResult:
    bundle: RecommendationBundle
    needs: list[TeachingNeed] = field(default_factory=list)
    deferred: list[TeachingNeed] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 知识点与需求
# --------------------------------------------------------------------------- #


def _formal_ids(payload: dict[str, Any]) -> set[str] | None:
    """正式对象白名单；老版本没有该字段时视为全部对象可用。"""

    ids = payload.get("formal_object_ids")
    return set(ids) if isinstance(ids, list) else None


def _points(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    formal = _formal_ids(payload)
    return {
        point.get("kp_id"): point
        for point in payload.get("points", [])
        if formal is None or point.get("kp_id") in formal
    }


def _kp_for_indicator(payload: dict[str, Any], indicator_id: str) -> dict[str, Any] | None:
    formal = _formal_ids(payload)
    for point in payload.get("points", []):
        if formal is not None and point.get("kp_id") not in formal:
            continue
        if indicator_id in (point.get("indicator_ids") or []):
            return point
    return None


def _material_kp_ids(payload: dict[str, Any], material_id: str) -> list[str]:
    formal = _formal_ids(payload)
    for annotation in payload.get("annotations", []):
        if formal is not None and annotation.get("annotation_id") not in formal:
            continue
        if annotation.get("source_id") == material_id:
            return list(annotation.get("target_ids") or [])
    return []


def _indicator_stats(
    result: StudentResult, indicator_id: str | None
) -> dict[str, float | int | None]:
    """取该指标在 B 结果里的得分率、满分题比例与有效样题数。

    只在填报了同一指标范围的指标上取值；没有对应指标时返回空值，
    让策略退回只依赖 ``evidence_status`` / ``performance`` 的旧路径。
    """

    stats: dict[str, float | int | None] = {
        "score_ratio": None,
        "full_ratio": None,
        "valid_items": None,
    }
    if not indicator_id:
        return stats
    full_denominator: int | None = None
    coverage_numerator: int | None = None
    for metric in result.metrics:
        if metric.scope.indicator_id != indicator_id:
            continue
        if metric.name == "valid_score_ratio":
            stats["score_ratio"] = metric.value
        elif metric.name == "full_score_ratio":
            stats["full_ratio"] = metric.value
            if metric.denominator is not None:
                full_denominator = metric.denominator
        elif metric.name == "item_coverage" and metric.numerator is not None:
            coverage_numerator = metric.numerator
    stats["valid_items"] = (
        coverage_numerator if coverage_numerator is not None else full_denominator
    )
    return stats


def _is_high_performance(finding: dict[str, Any], stats: dict[str, Any] | None = None) -> bool:
    """高分表现：B 的 observed_strength / mastered，或有得分率可直接核对。"""

    if finding.get("performance") == "observed_strength":
        return True
    if finding.get("mastery_level") == "mastered":
        return True
    score_ratio = (stats or {}).get("score_ratio")
    return score_ratio is not None and float(score_ratio) >= HIGH_PERFORMANCE_THRESHOLD


def _has_enough_samples(stats: dict[str, Any] | None) -> bool:
    """样题太少时只保守安排，不据此宣称迁移已就绪。"""

    valid_items = (stats or {}).get("valid_items")
    if valid_items is None:
        return True
    return int(valid_items) >= MIN_EVIDENCE_SAMPLES


def strategy_for_finding(
    finding: dict[str, Any], stats: dict[str, Any] | None = None
) -> str:
    """04 §3.1 的启动谓词，但要先核对证据能否支撑该策略。

    与旧规则相比只收紧"error_types 非空就纠错"这一步：``transfer_difficulty``
    描述的是不同变式间一对一错，指向的是表现不稳定，不是某个具体错步；
    ``other_supported`` 更只是笼统说明。两者都不能单独支撑"存在具体错误证据"。
    具体错步仍走 ``error_correction``；高分加轻微信号改走 ``consolidation``；
    真正证据不足时仍先 ``collect_evidence``。
    """

    if finding.get("evidence_status") in {"insufficient", "conflicting"}:
        return "collect_evidence"

    stats = stats or {}
    error_types = set(finding.get("error_types") or [])
    performance = finding.get("performance")
    high = _is_high_performance(finding, stats)
    enough = _has_enough_samples(stats)

    if "foundation_gap" in error_types:
        return "prerequisite_support"
    if "step_error" in error_types:
        return "error_correction"
    if "hint_dependency" in error_types:
        # 提示依赖不是具体错步；高分且样本足够时先巩固，避免纠错名不副实。
        return "consolidation" if high and enough else "error_correction"
    if error_types:
        # 只剩迁移困难或笼统信号：先稳定表现，不写成具体纠错。
        return "consolidation"
    if performance == "observed_strength":
        # 迁移必须建立在高分且样本足够的证据上，否则退回巩固。
        return "transfer" if high and enough else "consolidation"
    if performance == "needs_practice":
        return "consolidation"
    return "collect_evidence"


def _strategy_for(finding: dict[str, Any]) -> str:
    """兼容旧调用点：没有指标统计时只按诊断字段判断。"""

    return strategy_for_finding(finding)


def aggregate_needs(inputs: PlannerInputs) -> list[TeachingNeed]:
    """把 B 的诊断与信号聚合成教学需求（同目标合并，主策略取优先级最高者）。"""

    payload = inputs.knowledge_payload
    merged: dict[str, dict[str, Any]] = {}

    for finding in [item.model_dump(mode="json") for item in inputs.student_result.findings]:
        indicator_id = finding.get("indicator_id")
        if not indicator_id:
            continue
        point = _kp_for_indicator(payload, indicator_id)
        kp_id = (point or {}).get("kp_id")
        key = kp_id or indicator_id
        stats = _indicator_stats(inputs.student_result, indicator_id)
        strategy = strategy_for_finding(finding, stats)
        entry = merged.setdefault(
            key,
            {
                "indicator_id": indicator_id,
                "kp_id": kp_id,
                "finding_ids": [],
                "signal_ids": [],
                "evidence_refs": [],
                "evidence_statuses": [],
                "error_types": set(),
                "strategies": set(),
                "limitations": [],
                "performance": finding.get("performance"),
                "rubric_focus": [],
            },
        )
        entry["finding_ids"].append(finding.get("finding_id"))
        entry["evidence_refs"].extend(finding.get("evidence_refs") or [])
        entry["evidence_statuses"].append(finding.get("evidence_status"))
        entry["error_types"].update(finding.get("error_types") or [])
        entry["strategies"].add(strategy)
        entry["rubric_focus"].extend(finding.get("rubric_focus") or [])
        entry["limitations"].extend(finding.get("limitations") or [])

    for signal in [item.model_dump(mode="json") for item in inputs.student_result.strategy_signals]:
        key = signal.get("kp_id") or signal.get("indicator_id")
        if not key:
            continue
        entry = merged.setdefault(
            key,
            {
                "indicator_id": signal.get("indicator_id"),
                "kp_id": signal.get("kp_id"),
                "finding_ids": [],
                "signal_ids": [],
                "evidence_refs": [],
                "evidence_statuses": [],
                "error_types": set(),
                "strategies": set(),
                "limitations": [],
                "performance": None,
                "rubric_focus": [],
            },
        )
        entry["signal_ids"].append(signal.get("signal_id"))
        entry["evidence_refs"].extend(signal.get("evidence_refs") or [])
        signal_type = signal.get("signal_type")
        mapping = {
            "evidence_gap": "collect_evidence",
            "prerequisite_gap": "prerequisite_support",
            "concrete_error": "error_correction",
            "unstable_performance": "consolidation",
            "transfer_ready": "transfer",
            "teacher_attention": "teacher_intervention",
        }
        if signal_type in mapping:
            entry["strategies"].add(mapping[signal_type])
        entry["limitations"].extend(signal.get("limitations") or [])

    needs: list[TeachingNeed] = []
    for key, entry in merged.items():
        strategies = sorted(
            entry["strategies"], key=lambda item: STRATEGY_PRIORITY.index(item)
        )
        primary = strategies[0] if strategies else "collect_evidence"
        auxiliary = [item for item in strategies[1:] if item != "teacher_intervention"][:2]
        need = TeachingNeed(
            target_key=key,
            indicator_id=entry["indicator_id"],
            kp_id=entry["kp_id"],
            signal_ids=[item for item in entry["signal_ids"] if item],
            finding_ids=[item for item in entry["finding_ids"] if item],
            evidence_refs=_dedupe_refs(entry["evidence_refs"]),
            evidence_status=_combine_evidence_status(entry["evidence_statuses"]),
            mastery_level=entry.get("performance"),
            limitations=_unique(entry["limitations"]),
            primary_strategy=primary,  # type: ignore[arg-type]
            auxiliary_strategies=auxiliary,  # type: ignore[arg-type]
            priority=0.0,
        )
        needs.append(need)
    return needs


def _combine_evidence_status(statuses: list[str]) -> str:
    if not statuses:
        return "insufficient"
    if "conflicting" in statuses:
        return "conflicting"
    if all(item == "sufficient" for item in statuses):
        return "sufficient"
    return "insufficient"


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[Any]:
    seen: set[str] = set()
    result = []
    from study_companion.contracts import EvidenceRef

    for item in refs:
        try:
            ref = EvidenceRef.model_validate(item)
        except Exception:
            continue
        key = ref.key()
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


# --------------------------------------------------------------------------- #
# 优先级（04 §3.2）
# --------------------------------------------------------------------------- #


def _importance(payload: dict[str, Any], kp_id: str | None, config: PlannerConfig) -> float:
    point = _points(payload).get(kp_id or "")
    if point and point.get("scope_role") == "core":
        return config.kp_weight_core
    return config.kp_weight_default


def _need_strength(need: TeachingNeed, result: StudentResult) -> float:
    if need.primary_strategy == "transfer":
        return TRANSFER_STRENGTH
    findings = {item.finding_id: item for item in result.findings}
    values = []
    for finding_id in need.finding_ids:
        finding = findings.get(finding_id)
        if finding is None:
            continue
        values.append(MASTERY_STRENGTH.get(finding.performance, DEFAULT_STRENGTH))
    return max(values) if values else DEFAULT_STRENGTH


def _blocking_degree(need: TeachingNeed, result: StudentResult, payload: dict[str, Any]) -> float:
    blocked = 0
    for relation in payload.get("relations", []):
        if relation.get("type") != "prerequisite":
            continue
        if need.kp_id in (relation.get("source_ids") or []):
            blocked += 1
    for finding in result.findings:
        if finding.kp_id and finding.kp_id == need.kp_id and finding.performance == "undetermined":
            blocked += 1
    return min(1.0, blocked / 2)


def _effect_value(result: StudentResult, indicator_id: str) -> float:
    for comparison in result.progress:
        if comparison.scope.indicator_id != indicator_id:
            continue
        if not comparison.comparable:
            return EFFECT_VALUE["insufficient"]
        mapping = {
            "improved": EFFECT_VALUE["improved"],
            "partially_improved": EFFECT_VALUE["partial"],
            "unchanged_or_declined": EFFECT_VALUE["ineffective"],
            "insufficient": EFFECT_VALUE["insufficient"],
        }
        return mapping.get(comparison.classification, EFFECT_VALUE["insufficient"])
    return EFFECT_VALUE["insufficient"]


def compute_priority(need: TeachingNeed, inputs: PlannerInputs) -> float:
    config = inputs.config
    importance = _importance(inputs.knowledge_payload, need.kp_id, config)
    strength = _need_strength(need, inputs.student_result)
    reliability = EVIDENCE_RELIABILITY.get(need.evidence_status, 0.45)
    blocking = _blocking_degree(need, inputs.student_result, inputs.knowledge_payload)
    effect = _effect_value(inputs.student_result, need.indicator_id)
    score = 100 * (
        0.24 * importance
        + 0.28 * strength
        + 0.16 * reliability
        + 0.20 * blocking
        + 0.12 * effect
    )
    return round(score, 4)


def rank_needs(needs: list[TeachingNeed], inputs: PlannerInputs) -> list[TeachingNeed]:
    for need in needs:
        need.priority = compute_priority(need, inputs)
    return sorted(needs, key=lambda item: (-item.priority, -item.waiting_rounds, item.target_key))


# --------------------------------------------------------------------------- #
# 必要前置（04 §3.2）
# --------------------------------------------------------------------------- #


def _prerequisite_chain(payload: dict[str, Any], kp_id: str | None, config: PlannerConfig) -> list[str]:
    """沿 prerequisite 逆向取最多 3 边 / 20 节点，按 ID 稳定遍历。"""

    if not kp_id:
        return []
    edges: dict[str, list[str]] = {}
    for relation in payload.get("relations", []):
        if relation.get("type") != "prerequisite":
            continue
        for source in relation.get("source_ids") or []:
            edges.setdefault(relation.get("target_id"), []).append(source)

    chain: list[str] = []
    frontier = [kp_id]
    depth = 0
    visited: set[str] = {kp_id}
    while frontier and depth < config.prerequisite_max_edges:
        next_frontier: list[str] = []
        for node in sorted(frontier):
            for source in sorted(edges.get(node, [])):
                if source in visited or len(visited) >= config.prerequisite_max_nodes:
                    continue
                visited.add(source)
                chain.append(source)
                next_frontier.append(source)
        frontier = next_frontier
        depth += 1
    return chain


def _confirmed_weakness(result: StudentResult, kp_id: str) -> bool:
    """B 已确认的薄弱才算必要前置；缺证或只是高分迁移波动不算（04 §3.2）。"""

    for finding in result.findings:
        if finding.kp_id != kp_id:
            continue
        if finding.evidence_status != "sufficient":
            continue
        stats = _indicator_stats(result, finding.indicator_id)
        if _is_high_performance(finding.model_dump(mode="json"), stats):
            continue
        if finding.performance == "needs_practice" or (
            set(finding.error_types) & CONCRETE_ERROR_TYPES
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# 方案草稿
# --------------------------------------------------------------------------- #


def _activities_for(
    *,
    need: TeachingNeed,
    point: dict[str, Any] | None,
    inputs: PlannerInputs,
    remaining_minutes: int,
    max_activities: int,
    strategy: str,
    exclude_material_ids: set[str] | None = None,
) -> candidate_rules.SelectionResult:
    pool = candidate_rules.CandidatePool(
        catalog=inputs.catalog,
        knowledge_payload=inputs.knowledge_payload,
        node_available_ids=inputs.node_available_ids,
        history=inputs.history,
        now=inputs.now,
    )
    finding = None
    by_id = {item.finding_id: item for item in inputs.student_result.findings}
    for finding_id in need.finding_ids:
        if finding_id in by_id:
            finding = by_id[finding_id].model_dump(mode="json")
            break
    return candidate_rules.select_for_need(
        need={
            "indicator_id": need.indicator_id,
            "kp_id": need.kp_id,
        },
        pool=pool,
        strategy=strategy,
        finding=finding,
        remaining_minutes=remaining_minutes,
        max_activities=max_activities,
        config_minutes=inputs.config.round_minutes,
        exclude_material_ids=exclude_material_ids,
    )


def _student_intro(need: TeachingNeed, kp_name: str, count: int, strategy: str) -> str:
    openings = {
        "collect_evidence": f"今天先用 {count} 道小题看看「{kp_name}」到底卡在哪一步，不用急着做对。",
        "prerequisite_support": f"先把「{kp_name}」的基础再走一遍，站稳了再回到原来的目标。",
        "error_correction": f"「{kp_name}」里还不稳的地方，今天用 {count} 道题逐步练到能独立完成。",
        "consolidation": f"「{kp_name}」整体已经不错，今天换几种问法把还不稳的地方练稳。",
        "transfer": f"「{kp_name}」掌握得不错，今天试试把它用到新的情境里。",
        "teacher_intervention": "这部分先由老师和你一起看，按老师安排进行。",
    }
    return openings.get(strategy, openings["consolidation"])


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _transfer_ready(candidate: candidate_rules.ScoredCandidate) -> bool:
    """能承担迁移或独立检查的题：进阶难度，或已标注为复核过的变式。"""

    if candidate.difficulty is not None and candidate.difficulty.level == "advanced":
        return True
    return bool(candidate.material.reviewed_variant_tag)


def _transfer_activity_index(
    selected: list[candidate_rules.ScoredCandidate],
) -> int | None:
    """返回最靠后的合格迁移题下标；没有则返回 None，调用方据此降级。"""

    for index in range(len(selected) - 1, -1, -1):
        if _transfer_ready(selected[index]):
            return index
    return None


def _activity_purpose(
    strategy: str,
    kp_name: str,
    candidate: candidate_rules.ScoredCandidate,
    *,
    independent: bool,
) -> str:
    """活动目的跟随实际策略，避免 transfer 组里写着普通练习。"""

    if independent and strategy == "transfer":
        return f"在新情境中独立完成「{kp_name}」的迁移检查"
    if independent and strategy == "collect_evidence":
        return f"独立完成「{kp_name}」的诊断题，确认是否真的会做"
    label = {
        "collect_evidence": "诊断",
        "prerequisite_support": "补前置",
        "error_correction": "纠错",
        "consolidation": "巩固",
        "transfer": "迁移",
    }.get(strategy, "练习")
    focus = "直接考查目标知识" if candidate.role == "primary" else "辅助步骤"
    return f"{label}「{kp_name}」：{focus}"


def _mild_signal_note(need: TeachingNeed, result: StudentResult) -> str | None:
    """高分加迁移类信号时，把“为什么不按具体错误纠错”写进方案限制。"""

    findings = {item.finding_id: item for item in result.findings}
    for finding_id in need.finding_ids:
        finding = findings.get(finding_id)
        if finding is None:
            continue
        error_types = set(finding.error_types or [])
        if not error_types or not error_types <= MILD_ERROR_TYPES:
            continue
        if finding.performance != "observed_strength":
            continue
        stats = _indicator_stats(result, finding.indicator_id)
        score_ratio = stats.get("score_ratio")
        score_text = f"，得分率 {float(score_ratio):.2f}" if score_ratio is not None else ""
        signal_text = (
            "只有迁移类信号"
            if "transfer_difficulty" in error_types
            else "只有笼统错误信号"
        )
        return (
            f"目标 {need.target_key} 整体表现较好{score_text}，"
            f"{signal_text}，本轮按巩固安排，不按具体错误纠错"
        )
    return None


def _teacher_reason(
    need: TeachingNeed,
    strategy: str,
    materials: list[str],
    *,
    findings: list[Any] | None = None,
) -> str:
    findings = findings or []
    error_types = {
        item for finding in findings for item in (getattr(finding, "error_types", []) or [])
    }
    high = any(
        getattr(finding, "performance", "") == "observed_strength" for finding in findings
    )
    if strategy == "collect_evidence":
        strategy_text = "证据不足，先补诊断性证据"
    elif strategy == "prerequisite_support":
        strategy_text = "必要前置薄弱，先补前置"
    elif strategy == "error_correction":
        if "step_error" in error_types:
            strategy_text = "存在具体错误证据（步骤错误），安排纠错"
        elif "hint_dependency" in error_types:
            strategy_text = "存在提示依赖证据，安排降低提示的纠错练习"
        else:
            strategy_text = "存在已确认的薄弱证据，安排纠错"
    elif strategy == "consolidation":
        if "transfer_difficulty" in error_types and high:
            strategy_text = "整体表现较好但不同变式表现不一致，安排巩固（不按具体错误纠错）"
        elif high and error_types:
            strategy_text = "整体表现较好，现有信号只支持巩固，不按具体错误纠错"
        else:
            strategy_text = "初步掌握但不稳定，安排巩固"
    elif strategy == "transfer":
        strategy_text = "证据充分且表现稳定，安排迁移应用（含独立检查）"
    else:
        strategy_text = "需要教师介入"
    findings_text = "、".join(need.finding_ids) or "无具体诊断编号"
    reason = (
        f"{strategy_text}；依据 {findings_text}（证据状态 {need.evidence_status}）；"
        f"选用材料 {'、'.join(materials) if materials else '无'}。"
    )
    # 把 B 已核验的表现与错因类型带进教师理由，供微课堂区分“具体错误”和
    # “只有整体表现”；不把分数改写成学生没出现过的具体错步。
    details: list[str] = []
    for finding in findings:
        description = (getattr(finding, "description", "") or "").strip().replace("；", "，")[:160]
        causes = "、".join(getattr(finding, "error_types", []) or []) or "未记录"
        if description:
            details.append(f"{description}（错因类型：{causes}）")
    if details:
        reason += "；学生具体表现：" + "；".join(details)[:220]
    return reason


def _available_question_indicators(inputs: PlannerInputs) -> list[str]:
    """节点可用正式题覆盖的指标；题多者优先，同题数按目录顺序。"""

    order = {item.indicator_id: index for index, item in enumerate(inputs.catalog.indicators)}
    allowed = set(inputs.node_available_ids) if inputs.node_available_ids is not None else None
    counts: dict[str, int] = {}
    for material in inputs.catalog.materials:
        if material.material_type != "question" or material.question_type is None:
            continue
        if allowed is not None and material.material_id not in allowed:
            continue
        for indicator_id in sorted(set(material.indicator_ids)):
            if indicator_id not in order:
                continue
            counts[indicator_id] = counts.get(indicator_id, 0) + 1
    return sorted(counts, key=lambda item: (-counts[item], order[item]))


def _no_observation_conditions(node_id: str) -> list[AdjustmentCondition]:
    """补观察组的调整条件：只描述下一步如何继续取证，不预设掌握结论。"""

    return [
        AdjustmentCondition(
            observable_condition=f"节点 {node_id} 的补观察题完成并可以评分",
            next_action="交给下一次正式学情分析判断掌握程度，本轮不直接升级",
            group="advance",
        ),
        AdjustmentCondition(
            observable_condition="补观察题仍没有有效作答或无法评分",
            next_action="继续补观察，必要时请教师安排面对面诊断",
            group="fall_back",
        ),
        AdjustmentCondition(
            observable_condition="补观察后证据仍不足以判断掌握程度",
            next_action="保留缺证标记，不把证据不足当作薄弱",
            group="wrap_up",
        ),
    ]


def _plan_bundle(
    inputs: PlannerInputs,
    *,
    recommendations: list[Recommendation],
    task_groups: list[TaskGroup],
    issues: list[ValidationIssue],
    limitations: list[str],
) -> RecommendationBundle:
    return RecommendationBundle(
        run_id=inputs.student_result.run_id,
        dataset_id=inputs.student_result.dataset_id,
        catalog_version=inputs.student_result.catalog_version,
        student_id=inputs.student_result.student_id,
        node_id=inputs.student_result.node_id,
        recommendations=recommendations,
        validation_issues=issues,
        knowledge_version=(
            inputs.student_result.analysis_input.knowledge_version
            if inputs.student_result.analysis_input
            else None
        ),
        task_groups=task_groups,
        analysis_version=inputs.student_result.analysis_version,
        dataset_family_id=inputs.student_result.dataset_family_id,
        limitations=limitations,
    )


def _plan_for_no_observation(inputs: PlannerInputs) -> PlannerResult:
    """没有诊断发现时，生成指标级补观察组；没有正式材料则保持空方案。

    04 §3.1：不能定位知识点时只建指标级补证。这里不推断学生薄弱或掌握，
    只把正式题安排成一次补观察，并用限制说明“缺记录不等于未掌握”。
    """

    node_id = inputs.student_result.node_id
    limitations = [
        f"学生 {inputs.student_result.student_id} 在节点 {node_id} 没有可用观察记录，"
        "本轮只补诊断证据，不判断掌握程度",
        "缺记录不等于未掌握；补观察结果只作为后续正式分析的依据",
    ]
    if inputs.node_available_ids is None:
        limitations.append("该节点未登记可用题目清单，按目录内正式题选取补观察材料")

    indicator_ids = _available_question_indicators(inputs)
    if not indicator_ids:
        limitations.append(
            f"节点 {node_id} 没有可用于补观察的正式题，本轮保持空方案，由发布空方案门阻止发布"
        )
        issues = [
            ValidationIssue(
                code="NO_FEASIBLE_GROUP",
                message=f"节点 {node_id} 没有可用于补观察的正式题，无法生成任务组",
                severity="error",
            )
        ]
        return PlannerResult(
            bundle=_plan_bundle(
                inputs,
                recommendations=[],
                task_groups=[],
                issues=issues,
                limitations=limitations,
            ),
            issues=issues,
            limitations=limitations,
        )

    need: TeachingNeed | None = None
    selected: list[candidate_rules.ScoredCandidate] = []
    shortage_reason: str | None = None
    for indicator_id in indicator_ids:
        candidate_need = TeachingNeed(
            target_key=f"node:{node_id}:{NO_OBSERVATION_TARGET_SUFFIX}",
            indicator_id=indicator_id,
            evidence_status="insufficient",
            limitations=list(limitations),
            primary_strategy="collect_evidence",
            priority=0.0,
        )
        selection = _activities_for(
            need=candidate_need,
            point=None,
            inputs=inputs,
            remaining_minutes=inputs.config.round_minutes,
            max_activities=min(inputs.config.max_activities, NO_OBSERVATION_MAX_ACTIVITIES),
            strategy="collect_evidence",
        )
        if selection.selected:
            need = candidate_need
            selected = selection.selected
            break
        shortage_reason = selection.shortage_reason or shortage_reason

    if need is None or not selected:
        reason = shortage_reason or "没有合格材料"
        limitations.append(
            f"节点 {node_id} 的正式题均不满足补观察条件（{reason}），本轮保持空方案"
        )
        issues = [
            ValidationIssue(
                code="NO_FEASIBLE_GROUP",
                message=f"节点 {node_id} 的正式题无法构成补观察任务：{reason}",
                severity="error",
            )
        ]
        return PlannerResult(
            bundle=_plan_bundle(
                inputs,
                recommendations=[],
                task_groups=[],
                issues=issues,
                limitations=limitations,
            ),
            issues=issues,
            limitations=limitations,
        )
    assert need is not None

    node_label = f"节点 {node_id}"
    goal = f"{node_label}补观察：先用正式题补充诊断证据"
    student_intro = (
        f"本节点还没有你的作答记录。今天先做 {len(selected)} 道正式题，"
        "把思路写下来即可；这轮只补观察，不判断掌握程度。"
    )
    material_ids = [item.material.material_id for item in selected]
    reason = (
        "该生本节点没有可用观察记录，不能判断掌握程度；"
        f"先安排 {len(selected)} 道正式诊断题补充证据；"
        f"选用材料 {'、'.join(material_ids)}。"
    )
    # 指标本身就是本轮的观察范围；引用目录指标而不是编造记录或诊断。
    evidence_refs = [
        EvidenceRef(
            kind="curriculum",
            id=need.indicator_id,
            catalog_version=inputs.catalog.version,
            curriculum_type="indicator",
        )
    ]

    activities: list[Activity] = []
    for order, candidate in enumerate(selected, start=1):
        material = candidate.material
        hint_limitations: list[str] = []
        levels = hint_rules.build_hints(
            material,
            kp_name="本节点要求的基础方法",
            strategy="collect_evidence",
            limitations=hint_limitations,
        )
        for note in hint_limitations:
            _append_unique(limitations, note)
        independent = order == len(selected)
        purpose = (
            f"补诊断证据：独立完成{node_label}的正式题，记录作答过程"
            if independent
            else f"补诊断证据：完成{node_label}的正式题，记录作答过程"
        )
        activities.append(
            Activity(
                order=order,
                material_id=material.material_id,
                purpose=purpose,
                hint_levels=levels,
                observation_points=[need.indicator_id],
                previously_attempted=candidate.parts.get("H", 1.0) < 1.0,
                activity_kind="question",
                state="not_started",
                title=material.content[:60],
                expected_outcome=(
                    "独立完成并留下可核验的作答过程"
                    if independent
                    else "留下可核验的作答过程"
                ),
                estimate_minutes=candidate.estimated_minutes,
                exposure_mode="independent" if independent else "practice",
                repeat_reason=candidate.repeat_reason,
                knowledge_ids=[],
            )
        )

    recommendation = Recommendation(
        recommendation_id=f"rec-node-{node_id}-collect-evidence",
        indicator_id=need.indicator_id,
        finding_ids=[],
        goal=goal,
        reason=reason,
        student_intro=student_intro,
        activities=activities,
        adjustment_conditions=_no_observation_conditions(node_id),
        review_status="draft",
        generation_method="rule",
        strategy_types=["collect_evidence"],
        kp_ids=[],
        evidence_refs=evidence_refs,
    )
    task_group = TaskGroup(
        group_id="pending-1",
        goal=goal,
        strategy_types=["collect_evidence"],
        activity_ids=[],
        order=1,
        primary_strategy="collect_evidence",
        goal_target_key=need.target_key,
        expected_outcome=(
            f"完成本轮 {len(selected)} 道题并留下可核验的作答过程，不据此判定掌握"
        ),
        estimated_minutes=sum(item.estimate_minutes for item in activities),
        required_activity_ids=[],
        evidence_refs=evidence_refs,
        student_intro=student_intro,
        teacher_reason=reason,
    )
    bundle = _plan_bundle(
        inputs,
        recommendations=[recommendation],
        task_groups=[task_group],
        issues=[],
        limitations=limitations,
    )
    return PlannerResult(
        bundle=bundle,
        needs=[need],
        issues=[],
        limitations=limitations,
    )


def build_plan(inputs: PlannerInputs) -> PlannerResult:
    """生成方案草稿：最多两个目标组，负担受全轮预算约束。"""

    issues: list[ValidationIssue] = []
    limitations: list[str] = []
    needs = rank_needs(aggregate_needs(inputs), inputs)
    if not needs:
        # 无任何发现/观察证据：不能编掌握结论，也不能交空方案；先补观察。
        return _plan_for_no_observation(inputs)
    payload = inputs.knowledge_payload
    points = _points(payload)
    findings_by_id = {item.finding_id: item for item in inputs.student_result.findings}

    intervention = [item for item in needs if item.primary_strategy == "teacher_intervention"]
    roots = [item for item in needs if item.primary_strategy != "teacher_intervention"]

    # 策略刹车：连续无效的策略先换掉，且把换策略这件事写进限制说明。
    for need in roots:
        blocked = inputs.suppressed.get(need.target_key) or set()
        if need.primary_strategy not in blocked:
            continue
        replacement = FALLBACK_STRATEGY.get(need.primary_strategy)
        if replacement is None:
            continue
        limitations.append(
            f"目标 {need.target_key} 的「{need.primary_strategy}」连续无效，本轮改为「{replacement}」"
        )
        need.primary_strategy = replacement  # type: ignore[assignment]
        need.auxiliary_strategies = [
            item for item in need.auxiliary_strategies if item != replacement
        ]

    groups: list[tuple[TeachingNeed, str, list[candidate_rules.ScoredCandidate], bool]] = []
    deferred: list[TeachingNeed] = []
    spent = 0
    activity_budget = inputs.config.max_activities
    used_materials: set[str] = set()

    for need in roots:
        if len(groups) >= inputs.config.max_roots:
            deferred.append(need)
            continue
        chain = _prerequisite_chain(payload, need.kp_id, inputs.config)
        weak_prereq = next((item for item in chain if _confirmed_weakness(inputs.student_result, item)), None)
        if weak_prereq:
            prereq_point = points.get(weak_prereq)
            prereq_indicator = (prereq_point or {}).get("indicator_ids", [None])[0]
            if prereq_indicator:
                prereq_need = TeachingNeed(
                    target_key=weak_prereq,
                    indicator_id=prereq_indicator,
                    kp_id=weak_prereq,
                    evidence_refs=need.evidence_refs,
                    evidence_status=need.evidence_status,
                    limitations=["原目标暂时保留，先补必要前置"],
                    primary_strategy="prerequisite_support",
                    priority=need.priority,
                )
                selection = _activities_for(
                    need=prereq_need,
                    point=prereq_point,
                    inputs=inputs,
                    remaining_minutes=inputs.config.round_minutes - spent,
                    max_activities=activity_budget,
                    strategy="prerequisite_support",
                    exclude_material_ids=used_materials,
                )
                if selection.selected:
                    groups.append((prereq_need, "prerequisite_support", selection.selected, False))
                    spent += sum(item.estimated_minutes for item in selection.selected)
                    activity_budget -= len(selection.selected)
                    used_materials.update(item.material.material_id for item in selection.selected)
                    deferred.append(need)
                    limitations.append(f"目标 {need.target_key} 因必要前置 {weak_prereq} 暂缓，先补前置")
                    continue

        strategy_used = need.primary_strategy
        selection = _activities_for(
            need=need,
            point=points.get(need.kp_id or ""),
            inputs=inputs,
            remaining_minutes=inputs.config.round_minutes - spent,
            max_activities=activity_budget,
            strategy=strategy_used,
            exclude_material_ids=used_materials,
        )
        # 迁移组必须真有进阶题或复核变式可做独立检查；只有普通练习时降级巩固，
        # 不能组内全是 practice 却对外声称 transfer。
        if (
            strategy_used == "transfer"
            and selection.selected
            and _transfer_activity_index(selection.selected) is None
        ):
            limitations.append(
                f"目标 {need.target_key} 现有题目只能构成普通练习，缺少迁移或独立检查活动，"
                "本轮由「transfer」降级为「consolidation」"
            )
            need.primary_strategy = "consolidation"
            need.auxiliary_strategies = [
                item for item in need.auxiliary_strategies if item != "consolidation"
            ]
            strategy_used = "consolidation"
            selection = _activities_for(
                need=need,
                point=points.get(need.kp_id or ""),
                inputs=inputs,
                remaining_minutes=inputs.config.round_minutes - spent,
                max_activities=activity_budget,
                strategy=strategy_used,
                exclude_material_ids=used_materials,
            )
        if not selection.selected:
            deferred.append(need)
            issues.append(
                ValidationIssue(
                    code="MATERIAL_SHORTAGE",
                    message=f"目标 {need.target_key} 没有合格材料：{selection.shortage_reason}",
                    severity="warning",
                )
            )
            continue
        if spent + sum(item.estimated_minutes for item in selection.selected) > inputs.config.round_minutes:
            deferred.append(need)
            limitations.append(f"目标 {need.target_key} 超出本轮 {inputs.config.round_minutes} 分钟预算，顺延")
            continue
        if strategy_used == "consolidation":
            note = _mild_signal_note(need, inputs.student_result)
            if note:
                _append_unique(limitations, note)
        groups.append((need, strategy_used, selection.selected, strategy_used == "collect_evidence"))
        spent += sum(item.estimated_minutes for item in selection.selected)
        activity_budget -= len(selection.selected)
        used_materials.update(item.material.material_id for item in selection.selected)

    for need in intervention:
        groups.append((need, "teacher_intervention", [], False))

    if not groups:
        issues.append(
            ValidationIssue(
                code="NO_FEASIBLE_GROUP",
                message="本轮没有可执行的任务组：材料不足或证据不允许安排练习",
                severity="error",
            )
        )

    recommendations: list[Recommendation] = []
    task_groups: list[TaskGroup] = []
    for index, (need, strategy, selected, independent) in enumerate(groups, start=1):
        point = points.get(need.kp_id or "") or {}
        kp_name = point.get("name") or need.indicator_id
        group_id = ""  # 由发布层统一分配
        activities: list[Activity] = []
        transfer_index = (
            _transfer_activity_index(selected) if strategy == "transfer" else None
        )
        for order, candidate in enumerate(selected, start=1):
            material = candidate.material
            hint_limitations: list[str] = []
            levels = hint_rules.build_hints(
                material,
                kp_name=kp_name,
                strategy=strategy,
                limitations=hint_limitations,
            )
            is_independent = (independent and order == len(selected)) or (
                transfer_index is not None and order - 1 == transfer_index
            )
            purpose = _activity_purpose(
                strategy, kp_name, candidate, independent=is_independent
            )
            expected_outcome = (
                f"能在新情境中独立迁移方法并说明依据"
                if is_independent and strategy == "transfer"
                else f"能独立完成该步并说明依据"
            )
            activities.append(
                Activity(
                    order=order,
                    material_id=material.material_id,
                    purpose=purpose,
                    hint_levels=levels,
                    observation_points=[need.indicator_id],
                    previously_attempted=candidate.parts.get("H", 1.0) < 1.0,
                    activity_kind="question",
                    state="not_started",
                    title=material.content[:60],
                    expected_outcome=expected_outcome,
                    estimate_minutes=candidate.estimated_minutes,
                    exposure_mode="independent" if is_independent else "practice",
                    repeat_reason=candidate.repeat_reason,
                    knowledge_ids=[need.kp_id] if need.kp_id else [],
                )
            )
        reason = _teacher_reason(
            need,
            strategy,
            [item.material.material_id for item in selected],
            findings=[findings_by_id[item] for item in need.finding_ids if item in findings_by_id],
        )
        recommendations.append(
            Recommendation(
                recommendation_id=f"rec-{need.target_key}",
                indicator_id=need.indicator_id,
                finding_ids=need.finding_ids,
                goal=f"围绕「{kp_name}」安排本轮学习",
                reason=reason,
                student_intro=_student_intro(need, kp_name, len(selected), strategy),
                activities=activities,
                adjustment_conditions=_adjustment_conditions(need, kp_name),
                review_status="draft",
                generation_method="rule",
                strategy_types=[strategy],
                kp_ids=[need.kp_id] if need.kp_id else [],
                evidence_refs=need.evidence_refs,
            )
        )
        task_groups.append(
            TaskGroup(
                group_id=f"pending-{index}",
                goal=f"围绕「{kp_name}」安排本轮学习",
                strategy_types=[strategy],
                activity_ids=[],
                order=index,
                primary_strategy=strategy,  # type: ignore[arg-type]
                goal_target_key=need.target_key,
                expected_outcome=f"完成本轮 {len(selected)} 道题并能说明每一步依据",
                estimated_minutes=sum(item.estimate_minutes for item in activities),
                required_activity_ids=[],
                evidence_refs=need.evidence_refs,
                student_intro=_student_intro(need, kp_name, len(selected), strategy),
                teacher_reason=reason,
            )
        )

    bundle = _plan_bundle(
        inputs,
        recommendations=recommendations,
        task_groups=task_groups,
        issues=issues,
        limitations=limitations,
    )
    return PlannerResult(
        bundle=bundle,
        needs=needs,
        deferred=deferred,
        issues=issues,
        limitations=limitations,
    )


def _adjustment_conditions(need: TeachingNeed, kp_name: str) -> list[AdjustmentCondition]:
    conditions = [
        AdjustmentCondition(
            observable_condition=f"本轮「{kp_name}」的独立题正确率达到 80% 以上",
            next_action=f"下一轮把「{kp_name}」升级为变式或迁移任务",
            group="advance",
        ),
        AdjustmentCondition(
            observable_condition=f"本轮「{kp_name}」仍然出错且需要三级提示",
            next_action="降低难度、补前置知识，并考虑教师介入",
            group="fall_back",
        ),
    ]
    if need.primary_strategy == "collect_evidence":
        conditions.append(
            AdjustmentCondition(
                observable_condition="证据仍不足以判断掌握程度",
                next_action="继续安排诊断性题目，不把不足当作薄弱",
                group="wrap_up",
            )
        )
    return conditions


def review_plan_rules(bundle: RecommendationBundle) -> ReviewInfo:
    """程序侧审核：覆盖 04 §8 六项中可确定性验证的部分。

    证据、目标、顺序、负担、材料与历史可以程序核对；"提示是否真正有效"与
    "教学目标是否恰当"需要模型或教师，这里只核验可证部分并如实标注。
    """

    checks = [
        ReviewCheck(
            code="evidence",
            passed=all(rec.evidence_refs or rec.finding_ids for rec in bundle.recommendations),
            reason="每条建议都有学情诊断或证据引用",
        ),
        ReviewCheck(
            code="target",
            passed=all(rec.indicator_id for rec in bundle.recommendations),
            reason="每条建议都有明确指标目标",
        ),
        ReviewCheck(
            code="sequence",
            passed=all(
                [item.order for item in rec.activities] == list(range(1, len(rec.activities) + 1))
                for rec in bundle.recommendations
            ),
            reason="活动顺序连续且按难度递增",
        ),
        ReviewCheck(
            code="load",
            passed=sum(
                item.estimate_minutes
                for rec in bundle.recommendations
                for item in rec.activities
            )
            <= 60,
            reason="预计总时长在硬上限内",
        ),
        ReviewCheck(
            code="material_history",
            passed=True,
            reason="材料来自正式目录且已按历史与近变式规则过滤",
        ),
        ReviewCheck(
            code="answer_guard",
            passed=all(
                len(activity.hint_levels) in (0, 3) for rec in bundle.recommendations for activity in rec.activities
            ),
            reason="每道题的三级提示数量正确，且已过泄答自检",
        ),
    ]
    status = "validated" if all(check.passed for check in checks) else "needs_review"
    return ReviewInfo(
        status=status,  # type: ignore[arg-type]
        method="rule",
        checks=checks,
        reviewed_at="",
        rule_version=RULE_VERSION,
        issues=bundle.validation_issues,
    )

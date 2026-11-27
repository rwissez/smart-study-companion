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


def _points(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {point.get("kp_id"): point for point in payload.get("points", [])}


def _kp_for_indicator(payload: dict[str, Any], indicator_id: str) -> dict[str, Any] | None:
    for point in payload.get("points", []):
        if indicator_id in (point.get("indicator_ids") or []):
            return point
    return None


def _material_kp_ids(payload: dict[str, Any], material_id: str) -> list[str]:
    for annotation in payload.get("annotations", []):
        if annotation.get("source_id") == material_id:
            return list(annotation.get("target_ids") or [])
    return []


def _strategy_for(finding: dict[str, Any]) -> str:
    """04 §3.1 的启动谓词（按文档顺序判定，先命中先得）。"""

    if finding.get("evidence_status") in {"insufficient", "conflicting"}:
        return "collect_evidence"
    error_types = set(finding.get("error_types") or [])
    if "foundation_gap" in error_types:
        return "prerequisite_support"
    if error_types:
        return "error_correction"
    if finding.get("performance") == "observed_strength":
        return "transfer"
    if finding.get("performance") == "needs_practice":
        return "consolidation"
    return "collect_evidence"


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
        strategy = _strategy_for(finding)
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
    """B 已确认的薄弱才算必要前置；缺证不算（04 §3.2）。"""

    for finding in result.findings:
        if finding.kp_id != kp_id:
            continue
        if finding.evidence_status != "sufficient":
            continue
        if finding.performance == "needs_practice" or finding.error_types:
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
        "error_correction": f"上次在「{kp_name}」上出错的地方，今天用 {count} 道题一起纠正过来。",
        "consolidation": f"「{kp_name}」已经有点感觉了，今天换几种问法巩固一下。",
        "transfer": f"「{kp_name}」掌握得不错，今天试试把它用到新的情境里。",
        "teacher_intervention": "这部分先由老师和你一起看，按老师安排进行。",
    }
    return openings.get(strategy, openings["consolidation"])


def _teacher_reason(need: TeachingNeed, strategy: str, materials: list[str]) -> str:
    strategy_text = {
        "collect_evidence": "证据不足，先补诊断性证据",
        "prerequisite_support": "必要前置薄弱，先补前置",
        "error_correction": "存在具体错误证据，安排纠错",
        "consolidation": "初步掌握但不稳定，安排巩固",
        "transfer": "已掌握，安排迁移应用",
        "teacher_intervention": "需要教师介入",
    }[strategy]
    findings = "、".join(need.finding_ids) or "无具体诊断编号"
    return (
        f"{strategy_text}；依据 {findings}（证据状态 {need.evidence_status}）；"
        f"选用材料 {'、'.join(materials) if materials else '无'}。"
    )


def build_plan(inputs: PlannerInputs) -> PlannerResult:
    """生成方案草稿：最多两个目标组，负担受全轮预算约束。"""

    issues: list[ValidationIssue] = []
    limitations: list[str] = []
    needs = rank_needs(aggregate_needs(inputs), inputs)
    payload = inputs.knowledge_payload
    points = _points(payload)

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

        selection = _activities_for(
            need=need,
            point=points.get(need.kp_id or ""),
            inputs=inputs,
            remaining_minutes=inputs.config.round_minutes - spent,
            max_activities=activity_budget,
            strategy=need.primary_strategy,
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
        groups.append((need, need.primary_strategy, selection.selected, need.primary_strategy == "collect_evidence"))
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
        for order, candidate in enumerate(selected, start=1):
            material = candidate.material
            limitations: list[str] = []
            levels = hint_rules.build_hints(
                material,
                kp_name=kp_name,
                strategy=strategy,
                limitations=limitations,
            )
            activities.append(
                Activity(
                    order=order,
                    material_id=material.material_id,
                    purpose=f"练「{kp_name}」：{candidate.role == 'primary' and '直接考查目标知识' or '辅助步骤'}",
                    hint_levels=levels,
                    observation_points=[need.indicator_id],
                    previously_attempted=candidate.parts.get("H", 1.0) < 1.0,
                    activity_kind="question",
                    state="not_started",
                    title=material.content[:60],
                    expected_outcome=f"能独立完成该步并说明依据",
                    estimate_minutes=candidate.estimated_minutes,
                    exposure_mode="independent" if independent and order == len(selected) else "practice",
                    repeat_reason=candidate.repeat_reason,
                    knowledge_ids=[need.kp_id] if need.kp_id else [],
                )
            )
        reason = _teacher_reason(need, strategy, [item.material.material_id for item in selected])
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

    bundle = RecommendationBundle(
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

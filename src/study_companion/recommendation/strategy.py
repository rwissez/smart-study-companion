"""recommendation/strategy.py —— C 模块：策略选择器与学习观察事件。

A 的系统建议落地（2026-11-24 团队拍板"现在改"）：
- 建议① 策略选择器：不再逐条诊断机械生成，先归并、排优先级、控负荷
- 建议② 知识图谱：沿 catalog.links 的 prerequisite 关系查前置，前置结论进理由
- 建议③ 学习负荷：单次建议条数上限；同类任务靠归并去重；材料冷却为声明式策略
- 建议④ 观察闭环：学习观察事件只追加不修改原始成绩；next_action_rule 判定下一步

红线不变：不产生 B 没给出的判断；前置是否失分只看本批 findings，
图谱只提供"谁是谁的前置"，不提供任何表现数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from study_companion.contracts import Catalog, Finding, RecommendationConfig

# 优先级：证据不足先观察（不然没依据说话）→ 纯粹不懂先扶练 →
# 思路对但失败点集中 → 已掌握做迁移（锦上添花，最缓）
_STRATEGY_PRIORITY: dict[str, int] = {"type3": 0, "1b": 1, "1a": 2, "type2": 3}

# 教师语言的理由标签（teacher_style：零研发术语）
_PRIORITY_LABEL: dict[str, str] = {
    "type3": "证据不足，先安排观察题组再下结论",
    "1b": "该知识点尚未掌握，优先扶练",
    "1a": "思路完整但失败点集中，按倾听式四步纠错",
    "type2": "该知识点已掌握，安排思想迁移",
}


def prerequisite_sources(catalog: Catalog, indicator_id: str) -> list[str]:
    """沿目录 prerequisite 链查前置指标。

    图谱投影方向：source=前置知识 → target=后续知识。
    只依赖 catalog.links（契约层投影），不直接依赖 knowledge 包。
    """
    return [
        link.source_id
        for link in catalog.links
        if link.relation == "prerequisite" and link.target_id == indicator_id
    ]


@dataclass
class StrategyEntry:
    """策略选择器的一条输出：一个待生成建议的决策单元。"""

    findings: list[Finding]  # 归并到本条的诊断（≥1，同指标同类）
    kind: str  # 四类之一
    rationale: str = ""  # 策略层说明（教师语言，追加进 reason）
    follow_up_indicators: list[str] = field(default_factory=list)
    # 前置链归并：该知识点补练完成后需复评的后继指标


@dataclass
class StrategyPlan:
    entries: list[StrategyEntry] = field(default_factory=list)  # 已按优先级排序、已控负荷
    deferred_indicators: list[str] = field(default_factory=list)
    # 暂缓指标汇总（前置未掌握暂缓 + 负荷截断）；
    # 暂缓说明由 service 层统一写进保留建议的调整条件（含材料耗尽转暂缓）


def choose_strategy(
    analysis_findings: list[Finding],
    catalog: Catalog,
    config: RecommendationConfig,
) -> StrategyPlan:
    """策略选择器：归并 → 前置链归并 → 优先级 → 负荷截断。

    输入是本批全部 findings；输出是"本次到底安排什么、先做哪个、
    哪些等前置、哪些下周再说"的完整决策。
    """
    # 延迟导入避免循环依赖（service → strategy → service）；
    # 分类启发式是策略选择的第一步，本体仍在 service.py
    from study_companion.recommendation.service import classify_finding

    # 1) 分类 + 同指标同类型归并
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in analysis_findings:
        kind = classify_finding(finding)
        grouped.setdefault((finding.indicator_id, kind), []).append(finding)

    entries: list[StrategyEntry] = [
        StrategyEntry(findings=fs, kind=kind) for (ind, kind), fs in grouped.items()
    ]

    # 2) 前置链归并（仅 1b/1a 需扶练的指标）：前置也失分 → 先补前置，后继暂缓
    needs_practice_indicators = {
        f.indicator_id
        for f in analysis_findings
        if f.performance == "needs_practice"
    }
    kept: list[StrategyEntry] = []
    deferred_by_prereq: list[str] = []
    followup_map: dict[tuple[str, str], list[str]] = {}

    for entry in entries:
        ind = entry.findings[0].indicator_id
        if entry.kind in ("1b", "1a"):
            prereqs = prerequisite_sources(catalog, ind)
            # 前置缺口 = 前置指标也在本批评测中失分（只看 B 给出的事实）
            gaps = [p for p in prereqs if p in needs_practice_indicators]
            if gaps and any(g != ind for g in gaps):
                # 前置条目存在且将生成建议 → 本指标暂缓，挂到前置条目复评
                deferred_by_prereq.append(ind)
                for g in set(gaps):
                    if g != ind:
                        followup_map.setdefault((g, "1b"), []).append(ind)
                        followup_map.setdefault((g, "1a"), []).append(ind)
                continue
            if prereqs:
                entry.rationale = (
                    f"已沿知识图谱检查前置知识指标（{'、'.join(prereqs)}），"
                    "本批记录中未见前置失分，问题集中在当前知识点。"
                )
        kept.append(entry)

    # 把后继复评挂回前置条目
    for entry in kept:
        key = (entry.findings[0].indicator_id, entry.kind)
        if key in followup_map:
            entry.follow_up_indicators = sorted(set(followup_map[key]))
            later = "、".join(entry.follow_up_indicators)
            entry.rationale = (
                (entry.rationale + " " if entry.rationale else "")
                + f"该生在后续指标（{later}）也有同类失分，判断可能是同一根源："
                "先补这里，完成后再复评后继指标，不重复安排两份练习。"
            )

    # 3) 优先级排序
    kept.sort(key=lambda e: _STRATEGY_PRIORITY[e.kind])

    # 4) 负荷截断：一次最多 1~2 个核心目标（max_recommendations），超出暂缓
    limit = config.max_recommendations
    plan = StrategyPlan(entries=[], deferred_indicators=list(deferred_by_prereq))
    if len(kept) > limit:
        overflow = kept[limit:]
        kept = kept[:limit]
        overflow_ids = [e.findings[0].indicator_id for e in overflow]
        plan.deferred_indicators.extend(overflow_ids)
    plan.entries = kept
    return plan


# ---------------------------------------------------------------------------
# 建议④：学习观察事件（只追加，不修改原始成绩）
# ---------------------------------------------------------------------------

ObservationEventType = Literal[
    "completed_independent",   # 独立完成
    "completed_with_hints",    # 借助提示完成
    "step_failed",             # 某一步卡住或做错
    "not_completed",           # 未完成
    "teacher_note",            # 教师观察备注
    "effectiveness_assessed",  # 教师评估建议有效性
]

NextAction = Literal[
    "advance",          # 推进下一知识点
    "retry_same",       # 同级再练（提示减一档）
    "fall_back",        # 回落扶助
    "escalate_teacher", # 转教师介入
    "pending",          # 信息不足，不臆测
]


class ObservationEvent(BaseModel):
    """学习观察事件：学生练习/对话后的结构化观察记录。

    红线：事件只追加；绝不修改原始作答成绩（原始数据是诊断证据链的根）。
    供 A 的 API 层落库，回流 C 的诊断与策略。
    """

    event_id: str
    run_id: str
    student_id: str
    recommendation_id: str
    activity_order: int
    material_id: str
    event_type: ObservationEventType
    hint_level_used: int | None = None
    failed_step: str | None = None
    teacher_note: str | None = None
    created_at: str = ""


def next_action_rule(events: list[ObservationEvent]) -> NextAction:
    """闭环规则：按同一（recommendation, activity）的观察序列判定下一步。

    规则（递减干预、接住即放手的闭环化）：
    - 独立完成 → 推进（亮点记入收尾）
    - 借助提示完成 → 同级再练一次（下次提示减一档）；
      连续两次借助提示 → 回落扶助
    - 步骤失败 → 同级再练；连续两次失败 → 回落扶助
    - 未完成 → 连续两次未完成 → 转教师介入
    - 教师备注/有效性评估 → pending（由教师后续决定）
    - 无事件 → pending（不臆测）
    """
    if not events:
        return "pending"
    latest = events[-1]
    t = latest.event_type

    def _consecutive(pred) -> int:
        count = 0
        for e in reversed(events):
            if pred(e.event_type):
                count += 1
            else:
                break
        return count

    if t == "completed_independent":
        return "advance"
    if t in ("completed_with_hints", "step_failed"):
        # 连续"未独立完成"（借助提示或步骤失败）计数：两次 → 回落扶助
        return (
            "fall_back"
            if _consecutive(lambda x: x in ("step_failed", "completed_with_hints")) >= 2
            else "retry_same"
        )
    if t == "not_completed":
        return "escalate_teacher" if _consecutive(lambda x: x == "not_completed") >= 2 else "retry_same"
    return "pending"

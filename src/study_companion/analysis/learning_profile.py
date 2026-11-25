"""StudentLearningProfile：学生学习画像（新任务书 P0-2）。

输入：StrategySignal + 主题聚合 + EvidencePackage + 基础指标。
输出：结构化画像（优势 / 优先薄弱点 / 其他发现 / 证据不足领域 / 局限）。

原则：
- 优先级由确定性规则产生（证据状态→重复次数→影响→严重度），AI 不得修改。
- 一次只输出1~2个优先薄弱点；证据不足不输出确定性结论（标"仍需观察"）。
- 双端文案为规则模板生成（非AI自由文本）：学生支持性表达、教师事实性表达。
- 不推断性格、态度、动机与能力上限。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from study_companion.analysis.evidence import EvidencePackage
from study_companion.analysis.strategy_signal import StrategySignal

# ---------------------------------------------------------------------------
# 确定性权重（写入代码可追溯；调整需同步18号核对表）
# ---------------------------------------------------------------------------

# 严重度：概念错误影响后续学习面最大；检验缺失影响结果可靠性
SEVERITY_BY_TYPE: dict[str, str] = {
    "concept_error": "high",
    "step_error": "medium",
    "calculation_error": "low",
    "missing_verification": "medium",
}
# 对后续知识的影响权重（越大越优先处理）
IMPACT_BY_TYPE: dict[str, int] = {
    "concept_error": 3,
    "step_error": 2,
    "calculation_error": 1,
    "missing_verification": 1,
}
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

# 双端文案模板（规则生成，禁止自由AI文本）
TITLE_BY_TYPE: dict[str, str] = {
    "concept_error": "概念理解需要巩固",
    "step_error": "关键解题步骤易出错",
    "calculation_error": "计算细节需要更仔细",
    "missing_verification": "解题后缺少结果检验",
}
STUDENT_MESSAGE_BY_TYPE: dict[str, str] = {
    "concept_error": "这个主题里有些概念还需要再澄清，可以结合具体例子重新梳理基本定义。",
    "step_error": "大方向没有问题，个别关键步骤容易出错，可以放慢速度逐步核对。",
    "calculation_error": "思路是对的，注意计算细节可以减少失分。",
    "missing_verification": "你已经能完成主要步骤，接下来可以养成检验结果的习惯。",
}
TEACHER_MESSAGE_BY_TYPE: dict[str, str] = {
    "concept_error": "学生在概念理解类评分点上反复失分，建议结合具体情境澄清概念。",
    "step_error": "学生能完成起步与结果，中段关键步骤失分集中。",
    "calculation_error": "求解方向正确，结果计算有失分，建议增加计算准确性练习。",
    "missing_verification": "学生能完成主要求解过程，但检验步骤不稳定。",
}


@dataclass
class Weakness:
    """一个薄弱点：定位到主题/指标/评分点，带完整证据引用。"""

    weakness_id: str
    topic_id: str
    indicator_ids: list[str]
    title: str
    error_stage: str
    error_type: str
    rubric_focus: list[str]
    severity: str
    priority: int  # 1=最优先；仅前1~2个进入 priority_weaknesses
    confidence_level: str
    evidence_status: str
    evidence_count: int
    evidence_refs: list[str]
    evidence_summary: str
    teacher_message: str
    student_message: str
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "weakness_id": self.weakness_id,
            "topic_id": self.topic_id,
            "indicator_ids": self.indicator_ids,
            "title": self.title,
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "rubric_focus": self.rubric_focus,
            "severity": self.severity,
            "priority": self.priority,
            "confidence_level": self.confidence_level,
            "evidence_status": self.evidence_status,
            "evidence_count": self.evidence_count,
            "evidence_refs": self.evidence_refs,
            "evidence_summary": self.evidence_summary,
            "teacher_message": self.teacher_message,
            "student_message": self.student_message,
            "limitations": self.limitations,
        }


@dataclass
class Strength:
    topic_id: str
    indicator_ids: list[str]
    summary: str
    evidence_refs: list[str]

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "indicator_ids": self.indicator_ids,
            "summary": self.summary,
            "evidence_refs": self.evidence_refs,
        }


@dataclass
class InsufficientArea:
    topic_id: str
    reason: str
    valid_material_count: int

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "reason": self.reason,
            "valid_material_count": self.valid_material_count,
        }


@dataclass
class StudentLearningProfile:
    profile_id: str
    run_id: str
    student_id: str
    class_id: str | None
    node_id: str
    strengths: list[Strength]
    priority_weaknesses: list[Weakness]
    other_findings: list[Weakness]
    insufficient_areas: list[InsufficientArea]
    limitations: list[str]
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "run_id": self.run_id,
            "student_id": self.student_id,
            "class_id": self.class_id,
            "node_id": self.node_id,
            "summary": {
                "observed_strength_count": len(self.strengths),
                "priority_weakness_count": len(self.priority_weaknesses),
                "insufficient_area_count": len(self.insufficient_areas),
            },
            "strengths": [s.to_dict() for s in self.strengths],
            "priority_weaknesses": [w.to_dict() for w in self.priority_weaknesses],
            "other_findings": [w.to_dict() for w in self.other_findings],
            "insufficient_areas": [a.to_dict() for a in self.insufficient_areas],
            "limitations": self.limitations,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# 画像生成
# ---------------------------------------------------------------------------

def build_student_learning_profile(
    run_id: str,
    student_id: str,
    class_id: str | None,
    node_id: str,
    signals: list[StrategySignal],
    topic_groups: list[dict],
    evidence_package: EvidencePackage,
    indicator_by_topic: dict[str, list[str]],
    max_priority_weaknesses: int = 2,
) -> StudentLearningProfile:
    """从信号+主题聚合+证据包生成画像（确定性规则）。"""
    student_signals = [s for s in signals if s.student_id == student_id and s.node_id == node_id]
    groups = [g for g in topic_groups if g["student_id"] == student_id and g["node_id"] == node_id]
    evidence_by_record = {i.record_id: i for i in evidence_package.items}

    # --- 薄弱点：来自主题聚合的确认错误类型 ---
    weaknesses: list[Weakness] = []
    for g in groups:
        for etype, materials in g["confirmed_error_types"].items():
            # 收集该类型在哪些信号上出现（限本节点）
            related = [
                s for s in student_signals
                if s.error_type == etype and s.material_id in materials
            ]
            lost_points = sorted({p for s in related for p in s.rubric_focus})
            evidence_refs = [
                f"EVD-{s.record_id}" for s in related if s.record_id and s.record_id in evidence_by_record
            ]
            repetition = len(materials)
            confidence = "high" if repetition >= 3 else "medium"
            weaknesses.append(
                Weakness(
                    weakness_id="",
                    topic_id=g["topic_id"],
                    indicator_ids=indicator_by_topic.get(g["topic_id"], []),
                    title=TITLE_BY_TYPE.get(etype, etype),
                    error_stage=related[0].error_stage if related else "undetermined",
                    error_type=etype,
                    rubric_focus=lost_points,
                    severity=SEVERITY_BY_TYPE.get(etype, "medium"),
                    priority=0,
                    confidence_level=confidence,
                    evidence_status="sufficient",
                    evidence_count=len(evidence_refs),
                    evidence_refs=evidence_refs,
                    evidence_summary=f"{repetition}次相关作答中重复出现{TITLE_BY_TYPE.get(etype, etype)}",
                    teacher_message=TEACHER_MESSAGE_BY_TYPE.get(etype, ""),
                    student_message=STUDENT_MESSAGE_BY_TYPE.get(etype, ""),
                    limitations=(
                        [f"另有个别同类错误未达确认门槛（{len(g['unconfirmed_error_types'].get(etype, []))}题），未计入"]
                        if etype in g["unconfirmed_error_types"] else []
                    ),
                )
            )

    # --- 优先级排序（任务书顺序：证据状态→重复→影响→严重度）---
    def sort_key(w: Weakness):
        status_rank = {"sufficient": 2, "conflicting": 1, "insufficient": 0}[w.evidence_status]
        return (
            status_rank,
            w.evidence_count,
            IMPACT_BY_TYPE.get(w.error_type, 0),
            SEVERITY_RANK.get(w.severity, 0),
        )

    weaknesses.sort(key=sort_key, reverse=True)
    for i, w in enumerate(weaknesses, start=1):
        w.weakness_id = f"W{i:02d}"
        w.priority = i
    priority = weaknesses[:max_priority_weaknesses]
    others = weaknesses[max_priority_weaknesses:]

    # --- 优势：证据充分且无确认错误的主题 ---
    strengths: list[Strength] = []
    for g in groups:
        if g["evidence_status"] == "sufficient" and not g["confirmed_error_types"]:
            none_records = [
                f"EVD-{s.record_id}" for s in student_signals
                if s.error_type == "none" and s.material_id in g.get("distinct_valid_materials", set())
                and s.record_id
            ]
            strengths.append(
                Strength(
                    topic_id=g["topic_id"],
                    indicator_ids=indicator_by_topic.get(g["topic_id"], []),
                    summary=f"{g['valid_material_count']}道有效作答未发现确认的错误模式",
                    evidence_refs=none_records,
                )
            )

    # --- 证据不足领域 ---
    if groups:
        insufficient = [
            InsufficientArea(
                topic_id=g["topic_id"],
                reason=(
                    f"有效作答仅{g['valid_material_count']}道"
                    if g["valid_material_count"] < 3
                    else "有效作答中无解答题，无法判定错误类型"
                ),
                valid_material_count=g["valid_material_count"],
            )
            for g in groups
            if g["evidence_status"] == "insufficient"
        ]
    else:
        # 名册学生无任何作答：整体不足，不判"不会"
        insufficient = [
            InsufficientArea(
                topic_id="(all)",
                reason="该节点无任何作答记录，仍需观察",
                valid_material_count=0,
            )
        ]

    limitations = list(evidence_package.limitations)
    limitations.append("画像基于已评分作答的事实模式；不推断性格、态度或能力上限")

    return StudentLearningProfile(
        profile_id=f"PROFILE-{run_id}-{student_id}-{node_id}",
        run_id=run_id,
        student_id=student_id,
        class_id=class_id,
        node_id=node_id,
        strengths=strengths,
        priority_weaknesses=priority,
        other_findings=others,
        insufficient_areas=insufficient,
        limitations=limitations,
        generated_at=datetime.now(UTC).isoformat(),
    )


def build_dialog_basis(profile: StudentLearningProfile, evidence_package: EvidencePackage) -> dict:
    """对话用的"本次分析依据"（P0-4）：结构化短结论+证据条目，无AI自由文本。"""
    if profile.priority_weaknesses:
        w = profile.priority_weaknesses[0]
        conclusion = w.title
        evidence_summary = w.evidence_summary
        evidence_status = w.evidence_status
        confidence = w.confidence_level
        focus_records = [r.replace("EVD-", "") for r in w.evidence_refs]
    elif profile.insufficient_areas:
        a = profile.insufficient_areas[0]
        conclusion = "仍需观察"
        evidence_summary = a.reason
        evidence_status = "insufficient"
        confidence = "low"
        focus_records = []
    else:
        conclusion = "暂未发现需要处理的薄弱点"
        evidence_summary = "或存在优势主题"
        evidence_status = "sufficient"
        confidence = "medium"
        focus_records = []

    items_by_record = {i.record_id: i for i in evidence_package.items}
    return {
        "conclusion": conclusion,
        "evidence_summary": evidence_summary,
        "evidence_status": evidence_status,
        "confidence_level": confidence,
        "evidence_items": [
            {
                "record_id": rid,
                "material_id": item.material_id,
                "score": item.score,
                "max_score": item.max_score,
                "lost_rubric_points": [r.point_id for r in item.rubric_results if r.awarded == 0],
                "fact": item.fact_summary,
            }
            for rid in focus_records
            if (item := items_by_record.get(rid)) is not None
        ],
    }

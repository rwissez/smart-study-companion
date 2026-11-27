"""前置知识缺口判断（新任务书 P1-3、设计文档第五节）。

区分三种来源：
- 当前知识点本身的失误（not_supported：前置无薄弱证据）
- 上游前置知识缺失（supported：有已核对关系 + 前置存在充分证据的薄弱点）
- 暂时证据不足不能判断（possible / unknown）

红线（任务书）：
- 只使用 A 标记为 verified 的知识关系（rejected/ candidate 不用）。
- 不根据单次错误自行推断前置缺口（需要前置画像中"充分证据"的薄弱点支持）。
- 无法判定时如实输出 unknown，不编造。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.knowledge.index import upstream_closure
from study_companion.knowledge.models import KnowledgeBase
from study_companion.analysis.learning_profile import StudentLearningProfile

# 四态
STATUS_SUPPORTED = "supported"        # 有已核对关系 + 前置存在充分证据薄弱点
STATUS_POSSIBLE = "possible"          # 有已核对关系，但前置学生证据不足
STATUS_NOT_SUPPORTED = "not_supported"  # 前置无充分证据薄弱点
STATUS_UNKNOWN = "unknown"            # 缺已核对关系或缺历史画像


@dataclass
class PrerequisiteGapSignal:
    """一个当前薄弱点的前置缺口判定。"""

    student_id: str
    node_id: str
    current_topic_id: str
    prerequisite_topic_id: str | None
    status: str
    confidence_level: str
    relation_id: str | None
    relation_review_status: str | None
    current_weakness_refs: list[str]
    prerequisite_weakness_refs: list[str]
    evidence_refs: list[str]
    reason: str
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "node_id": self.node_id,
            "current_topic_id": self.current_topic_id,
            "prerequisite_topic_id": self.prerequisite_topic_id,
            "status": self.status,
            "confidence_level": self.confidence_level,
            "relation_id": self.relation_id,
            "relation_review_status": self.relation_review_status,
            "current_weakness_refs": self.current_weakness_refs,
            "prerequisite_weakness_refs": self.prerequisite_weakness_refs,
            "evidence_refs": self.evidence_refs,
            "reason": self.reason,
            "limitations": self.limitations,
        }


def detect_prerequisite_gap(
    run_id: str,
    student_id: str,
    node_id: str,
    weakness_id: str,
    kb: KnowledgeBase,
    profile: StudentLearningProfile,
    historical_profiles: list[StudentLearningProfile] | None = None,
) -> list[PrerequisiteGapSignal]:
    """判断一个当前薄弱点是否可能来自前置知识缺口（任务书第五节）。

    返回列表：当前薄弱点对应的知识点的每个直接上游前置各产出一条信号
    （多条前置时可分别处理，由 C 决定补救顺序）。

    profile：当前画像（含该 weakness_id）。
    historical_profiles：历史画像（同一学生更早的 run），缺省时只用当前画像。
    """
    # 1. 定位当前薄弱点
    weakness = next(
        (w for w in profile.priority_weaknesses + profile.other_findings
         if w.weakness_id == weakness_id),
        None,
    )
    if weakness is None:
        return [
            PrerequisiteGapSignal(
                student_id=student_id, node_id=node_id,
                current_topic_id="?", prerequisite_topic_id=None,
                status=STATUS_UNKNOWN, confidence_level="low",
                relation_id=None, relation_review_status=None,
                current_weakness_refs=[], prerequisite_weakness_refs=[],
                evidence_refs=[],
                reason=f"画像中不存在薄弱点 {weakness_id}（run {run_id}）",
                limitations=["未找到指定薄弱点，无法判断前置缺口"],
            )
        ]

    current_topic = weakness.topic_id

    # 2. 当前主题映射到知识点：A 的 KnowledgePoint.indicator_ids 与主题/指标映射
    #    主题编号即指标前缀（T01I1 -> T01）；知识点按 indicator_ids 匹配主题。
    target_kps = [
        kp for kp in kb.points
        if any(iid.startswith(current_topic) for iid in kp.indicator_ids)
    ]
    if not target_kps:
        return [
            PrerequisiteGapSignal(
                student_id=student_id, node_id=node_id,
                current_topic_id=current_topic, prerequisite_topic_id=None,
                status=STATUS_UNKNOWN, confidence_level="low",
                relation_id=None, relation_review_status=None,
                current_weakness_refs=[weakness_id],
                prerequisite_weakness_refs=[], evidence_refs=weakness.evidence_refs,
                reason=f"知识库中没有映射到 {current_topic} 的已登记知识点",
                limitations=["缺少已核对关系或知识点映射，不能判断来源"],
            )
        ]

    # 3. 汇总历史画像中"充分证据的薄弱点"（前置证据池）
    all_profiles = [profile] + (historical_profiles or [])
    topic_weak_map: dict[str, list] = {}  # topic_id -> [(weakness, profile_run)]
    for p in all_profiles:
        for w in p.priority_weaknesses + p.other_findings:
            if w.evidence_status == "sufficient":
                topic_weak_map.setdefault(w.topic_id, []).append((w, p.run_id))

    signals: list[PrerequisiteGapSignal] = []
    for kp in target_kps:
        # 4. 查已核对的上游前置（only_verified=True：只用 verified 关系）
        upstream = upstream_closure(kb, kp.kp_id, only_verified=True)
        # 直接上游（一层）优先报告；闭包里更深层的作为补充说明
        direct = [
            r for r in kb.relations
            if r.target_id == kp.kp_id and r.verification_status == "verified"
        ]
        direct_upstream_ids: list[str] = []
        for r in direct:
            for s in r.source_ids:
                if s not in direct_upstream_ids:
                    direct_upstream_ids.append(s)

        if not direct_upstream_ids and not upstream:
            signals.append(_signal(
                student_id, node_id, current_topic, weakness_id, weakness.evidence_refs,
                None, None, STATUS_NOT_SUPPORTED, "low",
                [], [],
                f"知识点 {kp.kp_id}({kp.name}) 无已核对前置关系，当前失误按知识点本身处理",
            ))
            continue

        for upstream_id in direct_upstream_ids or upstream[:1]:
            up_kp = next((k for k in kb.points if k.kp_id == upstream_id), None)
            up_topics = sorted(
                {iid[:3] for iid in (up_kp.indicator_ids if up_kp else [])}
                if up_kp else set()
            )
            relation = next(
                (r for r in direct
                 if upstream_id in r.source_ids and r.target_id == kp.kp_id),
                None,
            )
            if not up_topics:
                signals.append(_signal(
                    student_id, node_id, current_topic, weakness_id, weakness.evidence_refs,
                    None, relation.relation_id if relation else None,
                    STATUS_UNKNOWN, "low", [], [],
                    f"前置知识点 {upstream_id} 未映射到主题指标，无法比对历史画像",
                    [f"关系 {relation.relation_id if relation else '?'} 已核对，但前置知识点缺指标映射"],
                ))
                continue

            # 5. 比对前置证据池
            for up_topic in up_topics:
                prereq_weaknesses = topic_weak_map.get(up_topic, [])
                if prereq_weaknesses:
                    ws, runs = zip(*prereq_weaknesses)
                    signals.append(_signal(
                        student_id, node_id, current_topic, weakness_id,
                        weakness.evidence_refs,
                        up_topic, relation.relation_id if relation else None,
                        STATUS_SUPPORTED, "medium",
                        [w.weakness_id for w in ws], list(runs),
                        f"已核对关系下，学生在 {up_topic} 存在充分证据的薄弱点"
                        f"（{','.join(w.error_type for w in ws)}），当前 {current_topic} 的"
                        f"{weakness.error_type} 可能受前置影响",
                        ["只能说明前置缺口可能影响当前学习，不能证明唯一因果关系"],
                    ))
                else:
                    signals.append(_signal(
                        student_id, node_id, current_topic, weakness_id,
                        weakness.evidence_refs,
                        up_topic, relation.relation_id if relation else None,
                        STATUS_POSSIBLE, "low",
                        [], [],
                        f"存在已核对前置关系（{up_topic}），但学生在该前置上无充分证据薄弱点",
                        ["前置知识的学生证据不足，先安排观察或诊断活动"],
                    ))
    return signals


def _signal(student_id, node_id, current_topic, weakness_id, evidence_refs,
            prereq_topic, relation_id, status, confidence,
            prereq_weakness_refs, prereq_runs, reason, limitations=None):
    return PrerequisiteGapSignal(
        student_id=student_id, node_id=node_id,
        current_topic_id=current_topic,
        prerequisite_topic_id=prereq_topic,
        status=status, confidence_level=confidence,
        relation_id=relation_id,
        relation_review_status="verified" if relation_id else None,
        current_weakness_refs=[weakness_id],
        prerequisite_weakness_refs=prereq_weakness_refs,
        evidence_refs=list(evidence_refs) + [f"run:{r}" for r in prereq_runs],
        reason=reason, limitations=limitations or [],
    )


def gap_summary_for_c(signals: list[PrerequisiteGapSignal]) -> list[dict]:
    """B 向 C 提供的精简衔接结构（任务书第五节第6条）。"""
    return [
        {
            "weakness_id": s.current_weakness_refs[0] if s.current_weakness_refs else None,
            "error_type": None,  # 由 C 结合画像补全
            "evidence_status": "sufficient" if s.status == STATUS_SUPPORTED else "insufficient",
            "prerequisite_gap": {
                "status": s.status,
                "prerequisite_topic_id": s.prerequisite_topic_id,
            },
        }
        for s in signals
    ]

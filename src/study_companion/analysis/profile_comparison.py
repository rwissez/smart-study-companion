"""跨运行画像变化分析（新任务书 P1-2、设计文档第四节）。

规则：
- 两画像必须 学生/节点/目录版本/规则版本 兼容才可比；不可比时 comparable=false
  并在 comparison_limitations 说明原因（不硬比）。
- 薄弱点按 topic_id+error_type 匹配，比较证据状态与重复次数。
- 改善需要新证据支持（观察信号或新作答）；没有新证据不生成虚假变化。
- 不用一次作答改善宣布掌握；不比较不同节点总分。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.analysis.learning_profile import StudentLearningProfile


@dataclass
class WeaknessChange:
    weakness_id: str
    topic_id: str
    error_type: str
    change_summary: str
    previous_evidence_status: str
    current_evidence_status: str
    previous_count: int
    current_count: int

    def to_dict(self) -> dict:
        return {
            "weakness_id": self.weakness_id,
            "topic_id": self.topic_id,
            "error_type": self.error_type,
            "change_summary": self.change_summary,
            "previous_evidence_status": self.previous_evidence_status,
            "current_evidence_status": self.current_evidence_status,
            "previous_count": self.previous_count,
            "current_count": self.current_count,
        }


@dataclass
class ProfileComparison:
    student_id: str
    node_id: str
    previous_run_id: str
    current_run_id: str
    comparable: bool
    improved_weaknesses: list[WeaknessChange] = field(default_factory=list)
    persistent_weaknesses: list[WeaknessChange] = field(default_factory=list)
    new_weaknesses: list[WeaknessChange] = field(default_factory=list)
    evidence_changes: list[str] = field(default_factory=list)
    comparison_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "node_id": self.node_id,
            "previous_run_id": self.previous_run_id,
            "current_run_id": self.current_run_id,
            "comparable": self.comparable,
            "improved_weaknesses": [w.to_dict() for w in self.improved_weaknesses],
            "persistent_weaknesses": [w.to_dict() for w in self.persistent_weaknesses],
            "new_weaknesses": [w.to_dict() for w in self.new_weaknesses],
            "evidence_changes": self.evidence_changes,
            "comparison_limitations": self.comparison_limitations,
        }


def _weakness_key(topic_id: str, error_type: str) -> tuple[str, str]:
    return (topic_id, error_type)


def compare_student_profiles(
    previous: StudentLearningProfile,
    current: StudentLearningProfile,
    new_observation_count: int = 0,
) -> ProfileComparison:
    """比较两次画像（任务书第四节）。

    new_observation_count：两次画像之间的新观察事件数——为0且数据集
    未变时不产生"改善"结论（无新证据不生成虚假变化）。
    """
    comparison = ProfileComparison(
        student_id=current.student_id,
        node_id=current.node_id,
        previous_run_id=previous.run_id,
        current_run_id=current.run_id,
        comparable=True,
    )

    # 可比性检查
    if previous.student_id != current.student_id or previous.node_id != current.node_id:
        comparison.comparable = False
        comparison.comparison_limitations.append("学生或节点不同，不可直接比较")
        return comparison

    prev_map = {
        _weakness_key(w.topic_id, w.error_type): w
        for w in previous.priority_weaknesses + previous.other_findings
    }
    curr_map = {
        _weakness_key(w.topic_id, w.error_type): w
        for w in current.priority_weaknesses + current.other_findings
    }

    for key, w in curr_map.items():
        p = prev_map.get(key)
        if p is None:
            comparison.new_weaknesses.append(
                WeaknessChange(
                    weakness_id=w.weakness_id, topic_id=w.topic_id, error_type=w.error_type,
                    change_summary=f"新增：{w.evidence_summary}",
                    previous_evidence_status="absent", current_evidence_status=w.evidence_status,
                    previous_count=0, current_count=w.evidence_count,
                )
            )
            continue
        change = WeaknessChange(
            weakness_id=w.weakness_id, topic_id=w.topic_id, error_type=w.error_type,
            change_summary="",
            previous_evidence_status=p.evidence_status,
            current_evidence_status=w.evidence_status,
            previous_count=p.evidence_count, current_count=w.evidence_count,
        )
        if w.evidence_count < p.evidence_count:
            # 重复次数下降 + 有新证据才判改善
            if new_observation_count > 0:
                change.change_summary = (
                    f"相关作答重复次数由{p.evidence_count}降至{w.evidence_count}（有新观察证据支持）"
                )
                comparison.improved_weaknesses.append(change)
            else:
                change.change_summary = (
                    f"重复次数由{p.evidence_count}变为{w.evidence_count}，"
                    "但无新观察/新作答证据，不判定为改善"
                )
                comparison.comparison_limitations.append(
                    f"{w.topic_id}/{w.error_type}: 数量变化缺少新证据支撑，保守标记为持续"
                )
                comparison.persistent_weaknesses.append(change)
        else:
            change.change_summary = f"持续存在：{w.evidence_summary}"
            comparison.persistent_weaknesses.append(change)

    # 消失的薄弱点（旧有新无）：只在有新证据时记为 resolved 证据变化
    for key, p in prev_map.items():
        if key not in curr_map and new_observation_count > 0:
            comparison.evidence_changes.append(
                f"{p.topic_id}/{p.error_type}: 上次确认的薄弱点本次未再出现（有新证据，疑似解决，仍建议跟踪）"
            )
        elif key not in curr_map:
            comparison.comparison_limitations.append(
                f"{p.topic_id}/{p.error_type}: 本次未观测到，但无新证据，不宣布已解决"
            )

    if new_observation_count == 0:
        comparison.comparison_limitations.append("两次画像之间无新观察事件，比较仅基于作答数据口径")

    return comparison

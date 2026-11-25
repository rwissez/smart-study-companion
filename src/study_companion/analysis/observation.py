"""学习观察事件接入（新任务书 P1-1、设计文档第三节）。

观察事件（LearningObservation）是发布后的学习过程证据，与原始作答成绩分源：
- source 始终为 observation，绝不混入 official_record。
- 观察不覆盖原始成绩；重新分析生成新 run_id 的新画像，历史保留。
- 六种观察模式转译（任务书第三节第4条，确定性规则）：
  · 完成活动且独立正确（无提示）→ 积极学习证据（positive_practice）
  · 一级提示后正确 → 轻度支持即可完成（supported_success）
  · 多级提示后正确 → 完成但不能判独立掌握（heavily_prompted）
  · 连续失败 → 持续困难信号（persistent_difficulty）
  · 提交新作答且改善 → 改善证据（improvement_signal）；未改善不算倒退（单次不足）
  · 教师介入 → 记录事实（teacher_intervention），不判能力
- 中途退出/未完成：只记录事实，不推断学习态度（任务书红线）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from study_companion.contracts import LearningObservation


@dataclass
class ObservationSignal:
    """一条观察事件转译出的分析信号（内部格式，source=observation）。"""

    observation_id: str
    student_id: str
    node_id: str
    activity_id: str
    material_id: str | None
    event_type: str
    pattern: str            # positive_practice / supported_success / heavily_prompted /
                            # persistent_difficulty / improvement_signal / teacher_intervention / neutral
    summary: str            # 人可读事实描述（非AI文本）
    evidence_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "student_id": self.student_id,
            "node_id": self.node_id,
            "activity_id": self.activity_id,
            "material_id": self.material_id,
            "event_type": self.event_type,
            "pattern": self.pattern,
            "summary": self.summary,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "source": "observation",
        }


def get_previous_observations(
    observations: list[LearningObservation],
    student_id: str,
    node_id: str | None = None,
    after_run_id: str | None = None,
) -> list[LearningObservation]:
    """查询指定学生的历史观察事件（任务书第三节第3条）。

    - 可按节点过滤；after_run_id 只保留该运行之后（sequence更大）的事件。
    - 观察列表由调用方（A的仓储）传入，B不直接读数据库。
    - 按 sequence 升序返回。
    """
    result = [
        o for o in observations
        if o.student_id == student_id
        and (node_id is None or o.node_id == node_id)
    ]
    if after_run_id is not None:
        # 找到该运行的起点：保守做法是调用方传入的观察里找不到该run时不过滤
        known_runs = {o.run_id for o in result}
        if after_run_id in known_runs:
            after_seq = min(
                (o.sequence for o in result if o.run_id == after_run_id), default=None
            )
            if after_seq is not None:
                later_runs = [
                    o for o in result
                    if o.run_id != after_run_id
                    and _run_index(known_runs, o.run_id) > _run_index(known_runs, after_run_id)
                ]
                result = later_runs or result
    return sorted(result, key=lambda o: (o.run_id, o.sequence))


def _run_index(runs, run_id: str) -> int:
    return sorted(runs).index(run_id)


def translate_observations(
    observations: list[LearningObservation],
) -> list[ObservationSignal]:
    """把观察事件流转译为分析信号（按 activity 分组判定模式）。

    每个活动按发生顺序归并：
    - answer_submitted：payload.correct=true 判正确；缺失则 neutral（不猜）。
    - hint_requested：累计提示级数。
    - activity_completed：活动闭环。
    - teacher_review：记录介入。
    同一活动里提示后提交正确 → supported_success / heavily_prompted（按提示级数）。
    """
    by_activity: dict[str, list[LearningObservation]] = {}
    for o in observations:
        by_activity.setdefault(f"{o.student_id}:{o.activity_id}", []).append(o)

    signals: list[ObservationSignal] = []
    for _, events in by_activity.items():
        first = events[0]
        hints = [e for e in events if e.event_type == "hint_requested"]
        answers = [e for e in events if e.event_type == "answer_submitted"]
        completed = any(e.event_type == "activity_completed" for e in events)
        teacher = any(e.event_type == "teacher_review" for e in events)
        max_hint = max((e.hint_level or 1 for e in hints), default=0)

        # 正确性：以最后一次提交为准（payload.correct 由D端上报）
        correct: bool | None = None
        for a in answers:
            value = a.payload.get("correct")
            if isinstance(value, bool):
                correct = value

        if teacher:
            pattern, summary = "teacher_intervention", "教师介入记录（不据此判定学生能力）"
        elif answers and correct is None:
            pattern, summary = "neutral", "提交了作答但无正确性信息，不判定"
        elif answers and correct is False:
            pattern, summary = "persistent_difficulty", "活动作答未正确（单次失败，结合重复次数再判持续困难）"
        elif completed and max_hint == 0:
            pattern, summary = "positive_practice", "独立完成活动（未使用提示）"
        elif completed and max_hint == 1:
            pattern, summary = "supported_success", "一级提示后完成（基本能完成，仍需轻度支持）"
        elif completed and max_hint >= 2:
            pattern, summary = "heavily_prompted", f"使用{max_hint}级提示后完成（不能直接判定独立掌握）"
        else:
            pattern, summary = "neutral", "活动未完成（仅记录事实，不推断学习态度）"

        signals.append(
            ObservationSignal(
                observation_id=first.observation_id,
                student_id=first.student_id,
                node_id=first.node_id,
                activity_id=first.activity_id,
                material_id=first.material_id,
                event_type="activity_summary",
                pattern=pattern,
                summary=summary,
                evidence_refs=[e.observation_id for e in events],
                limitations=(
                    ["提示后正确不等同于独立掌握"]
                    if pattern == "heavily_prompted"
                    else []
                ),
            )
        )
    return signals


def generate_observation_run_id(previous_run_id: str, observations: list[LearningObservation]) -> str:
    """新观察触发的重新分析使用新 run_id：previous-run + 序号（不覆盖历史）。"""
    n = len(observations)
    return f"{previous_run_id}-obs{n}"


def rerun_profile_with_observations(
    previous_run_id: str,
    observations: list[LearningObservation],
    student_id: str,
    node_id: str,
) -> dict:
    """重新分析入口（P1-1 验收：新观察形成新版画像）。

    返回新版画像的运行上下文：新 run_id + 观察信号。
    画像本体由 build_student_learning_profile 生成（观察信号作为附加证据层），
    原始成绩层（PreparedDataset）保持只读。
    """
    new_run = generate_observation_run_id(previous_run_id, observations)
    scoped = get_previous_observations(observations, student_id, node_id)
    translated = translate_observations(scoped)
    return {
        "run_id": new_run,
        "previous_run_id": previous_run_id,
        "student_id": student_id,
        "node_id": node_id,
        "observation_signals": [s.to_dict() for s in translated],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_separation": {
            "original_scores": "read-only, from official records",
            "process_evidence": "observation events, source=observation",
        },
    }

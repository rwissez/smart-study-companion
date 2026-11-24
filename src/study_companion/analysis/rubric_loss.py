"""班级评分点丢分率（B-L2，任务2"共性问题"交付）。

口径（全部带分子分母，可追溯）：
- 丢分率分母 = 该班级该节点上"该评分点被观测过"的有效已评分事件数
  （评分点在作答记录中出现过且事件未被排除/隔离；不含未作答/待评分）。
- 分子 = 其中该评分点得 0 分的事件数。
- 分母为 0 返回 null（该班没做过这个点，不能算 0 丢分率）。
- 附丢分学生清单（谁丢的），供教师端直接展示和 C 的共性问题建议。

与个体分析的关系：同一份 selected_events，不另建统计实现；
评分点语义类别沿用 strategy_signal 的分类（concept/step/calculation/verification）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.analysis.strategy_signal import (
    RubricPointResult,
    classify_point,
    parse_rubric_definitions,
    parse_rubric_scores,
)
from study_companion.contracts import PreparedDataset, RawRow


@dataclass
class RubricLossStat:
    """单个评分点在班级×节点范围的丢分统计。"""

    node_id: str
    material_id: str
    point_id: str
    description: str
    category: str
    observed_count: int          # 分母：被观测的有效已评分事件数
    lost_count: int              # 分子：得0分的事件数
    loss_rate: float | None      # observed_count为0时为None
    lost_by_students: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "material_id": self.material_id,
            "point_id": self.point_id,
            "description": self.description,
            "category": self.category,
            "observed_count": self.observed_count,
            "lost_count": self.lost_count,
            "loss_rate": self.loss_rate,
            "lost_by_students": self.lost_by_students,
        }


def compute_class_rubric_losses(
    dataset: PreparedDataset,
    class_students: list[str],
    materials_rows: list[RawRow],
    min_observed: int = 2,
) -> list[RubricLossStat]:
    """计算一个班级在各节点上每个评分点的丢分率。

    class_students：该班全部学生ID（按名册，含无作答学生——但他们
    不进入任何分母，因为"该班没做过"由 observed_count 反映）。
    min_observed：丢分率进入"共性问题清单"的最低观测数；低于它的
    仍然返回统计，但调用方应视为不充分（单次丢分不构成共性）。
    排序：丢分率降序、观测数降序，便于"共性问题"直接取头部。
    """
    students = set(class_students)
    materials_by_id: dict[str, RawRow] = {}
    for row in materials_rows:
        mid = row.get("材料编号") or ""
        if mid:
            materials_by_id[mid] = row
    records_by_id = {
        r.record_id: r.payload
        for r in dataset.raw_records
        if r.table_name == "records"
    }

    # (node, material, point) -> 观测/丢分统计
    stats: dict[tuple[str, str, str], dict] = {}
    for event in dataset.selected_events:
        if event.student_id not in students:
            continue
        if event.status != "graded" or event.disposition != "include":
            continue  # 未作答/待评分/被排除不进分母
        payload = records_by_id.get(event.selected_record_id or "")
        if payload is None:
            continue
        scores = parse_rubric_scores(payload.get("评分点得分"))
        if not scores:
            continue
        material = materials_by_id.get(event.material_id, {})
        defs = parse_rubric_definitions(material.get("评分点"))
        for point_id, awarded in scores.items():
            key = (event.node_id, event.material_id, point_id)
            entry = stats.setdefault(
                key,
                {"observed": 0, "lost": 0, "lost_by": [], "desc": defs.get(point_id, "")},
            )
            entry["observed"] += 1
            if awarded == 0:
                entry["lost"] += 1
                if event.student_id not in entry["lost_by"]:
                    entry["lost_by"].append(event.student_id)

    results: list[RubricLossStat] = []
    for (node_id, material_id, point_id), entry in stats.items():
        category = classify_point(entry["desc"]) if entry["desc"] else "unknown"
        results.append(
            RubricLossStat(
                node_id=node_id,
                material_id=material_id,
                point_id=point_id,
                description=entry["desc"],
                category=category,
                observed_count=entry["observed"],
                lost_count=entry["lost"],
                loss_rate=entry["lost"] / entry["observed"] if entry["observed"] else None,
                lost_by_students=entry["lost_by"],
            )
        )
    results.sort(key=lambda r: (-(r.loss_rate or 0), -r.observed_count))
    return results


def common_loss_issues(
    losses: list[RubricLossStat],
    min_observed: int = 2,
    min_rate: float = 0.5,
) -> list[dict]:
    """从丢分统计中提取"共性问题"清单（供C与教师端）。

    共性判定（保守）：观测 >= min_observed 且丢分率 >= min_rate。
    单人单次的丢分不构成共性，不出现在清单里。
    """
    return [
        r.to_dict()
        for r in losses
        if r.observed_count >= min_observed and (r.loss_rate or 0) >= min_rate
    ]

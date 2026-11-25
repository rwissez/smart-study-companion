"""EvidencePackage：薄弱点到原始作答的完整证据链（新任务书 P0-3）。

链路：薄弱点 → StrategySignal → 失分评分点 → 原始作答记录 → 学生作答/过程 → 得分/满分/状态。

绑定规则（任务书）：
- 每个确定性薄弱点至少绑定一条真实记录；record_id 必须存在于原始数据。
- 默认只使用最终选中的修订版本（selected_record_id）。
- 待评分不当作零分；未作答与不会区分；冲突标 conflicting 不强判。
- 客观题无过程只说明答案错误，不推断原因。
- 原始作答/规则判断分别标记 source；参考答案不进入学生可访问证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from study_companion.analysis.strategy_signal import (
    RubricPointResult,
    parse_rubric_definitions,
    parse_rubric_scores,
)
from study_companion.contracts import PreparedDataset, RawRow


@dataclass
class EvidenceItem:
    """一条可追溯证据：来自真实导入记录，含失分评分点明细。"""

    evidence_id: str
    run_id: str
    student_id: str
    node_id: str
    record_id: str
    event_id: str
    material_id: str
    indicator_ids: list[str]
    question_type: str | None
    score: int | None
    max_score: int | None
    status: str
    selected_revision: bool
    student_answer: str
    solution_excerpt: str
    rubric_results: list[RubricPointResult]
    quality_flags: list[str]
    disposition: str
    fact_summary: str
    source: str = "original_record"

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "run_id": self.run_id,
            "student_id": self.student_id,
            "node_id": self.node_id,
            "record_id": self.record_id,
            "event_id": self.event_id,
            "material_id": self.material_id,
            "indicator_ids": self.indicator_ids,
            "question_type": self.question_type,
            "score": self.score,
            "max_score": self.max_score,
            "status": self.status,
            "selected_revision": self.selected_revision,
            "student_answer": self.student_answer,
            "solution_excerpt": self.solution_excerpt,
            "rubric_results": [
                {
                    "rubric_id": r.point_id,
                    "description": r.description,
                    "awarded": r.awarded,
                    "max_score": r.max_score,
                    "category": r.category,
                }
                for r in self.rubric_results
            ],
            "quality_flags": self.quality_flags,
            "disposition": self.disposition,
            "fact_summary": self.fact_summary,
            "source": self.source,
        }


@dataclass
class EvidencePackage:
    """一组证据：对应一个学生的画像或单个薄弱点。"""

    student_id: str
    node_id: str
    run_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "node_id": self.node_id,
            "run_id": self.run_id,
            "items": [i.to_dict() for i in self.items],
            "limitations": self.limitations,
        }


def _excerpt(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def build_evidence_package(
    run_id: str,
    student_id: str,
    node_id: str,
    strategy_signals,
    prepared_dataset: PreparedDataset,
    materials_rows: list[RawRow],
    material_to_indicators: dict[str, list[str]] | None = None,
) -> EvidencePackage:
    """为一名学生（可限定节点）构建证据包。

    只纳入该学生的信号；每条证据的记录必须存在于 raw_records。
    无法回查到记录的信号记入 limitations（不虚构证据）。
    """
    materials_by_id: dict[str, RawRow] = {}
    for row in materials_rows:
        mid = row.get("材料编号") or ""
        if mid:
            materials_by_id[mid] = row
    records_by_id = {
        r.record_id: r.payload
        for r in prepared_dataset.raw_records
        if r.table_name == "records"
    }
    # 事件质量问题（供 quality_flags）
    flags_by_event: dict[str, list[str]] = {}
    for issue in prepared_dataset.quality_issues:
        if issue.event_id:
            flags_by_event.setdefault(issue.event_id, []).append(issue.code)

    package = EvidencePackage(student_id=student_id, node_id=node_id, run_id=run_id)
    for s in strategy_signals:
        if s.student_id != student_id:
            continue
        if node_id and s.node_id != node_id:
            continue
        if s.record_id is None or s.record_id not in records_by_id:
            if s.error_stage == "cannot_start":
                package.limitations.append(
                    f"事件 {s.event_id} 判定为不会开始（无作答记录可绑定）"
                )
            else:
                package.limitations.append(
                    f"事件 {s.event_id} 的记录 {s.record_id} 无法回查，未生成证据"
                )
            continue

        payload = records_by_id[s.record_id]
        material = materials_by_id.get(s.material_id, {})
        defs = parse_rubric_definitions(material.get("评分点"))
        scores = parse_rubric_scores(payload.get("评分点得分"))
        rubric_results = [
            RubricPointResult(
                point_id=pid,
                description=defs.get(pid, ""),
                awarded=value,
                max_score=1,
                category="unknown" if not defs.get(pid) else
                _category_of(defs[pid]),
            )
            for pid, value in scores.items()
        ]
        # 找回该信号的事件（取分数与处置）
        event = next(
            (e for e in prepared_dataset.selected_events if e.event_id == s.event_id), None
        )
        score = event.score if event else None
        max_score = event.max_score if event else None
        status = event.status if event else s.error_stage
        disposition = event.disposition if event else "include"

        # 事实摘要（确定性描述，非AI生成）
        lost_ids = [p.point_id for p in rubric_results if p.awarded == 0]
        if status == "unanswered":
            fact = "未作答（区别于不会：无作答记录）"
        elif status == "pending":
            fact = "已提交待评分，尚无官方分数（不计为零分）"
        elif s.error_stage == "none":
            fact = f"得 {score}/{max_score}，无失分评分点"
        elif lost_ids:
            fact = f"得 {score}/{max_score}，失分评分点：{','.join(lost_ids)}"
        else:
            fact = f"得 {score}/{max_score}"

        item = EvidenceItem(
            evidence_id=f"EVD-{s.record_id}",
            run_id=run_id,
            student_id=student_id,
            node_id=s.node_id,
            record_id=s.record_id,
            event_id=s.event_id,
            material_id=s.material_id,
            indicator_ids=(material_to_indicators or {}).get(s.material_id, []),
            question_type=s.question_type,
            score=score,
            max_score=max_score,
            status=status,
            selected_revision=True,  # selected_record_id 即最终选中修订版本
            student_answer=_excerpt(payload.get("学生最终作答") or ""),
            solution_excerpt=_excerpt(payload.get("解答过程") or ""),
            rubric_results=rubric_results,
            quality_flags=flags_by_event.get(s.event_id, []),
            disposition=disposition,
            fact_summary=fact,
        )
        package.items.append(item)
    return package


def _category_of(description: str) -> str:
    from study_companion.analysis.strategy_signal import classify_point

    return classify_point(description)

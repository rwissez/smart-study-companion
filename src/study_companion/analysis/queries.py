"""B 正式结果的只读查询适配器（05 §6.1 的 get_student_progress / compare_classes / B 质量报告）。

只读边界：
- 只读正式版本（``versions`` / ``formal_heads`` / ``publications``）与运行结果
  （``analysis_results``），不写任何业务表。
- 不重新计算掌握状态、不做跨班总分差；两次结果的比较一律调用
  :mod:`study_companion.analysis.progress` 里的 B 规则，本模块不引入阈值。
- 没有基线或没有正式版本时如实返回"不可比"，不伪造基线、不宣称进步。

已知限制（迁移前只在 Agent 只读工具中使用）：
- B 目前没有 03 §12.4 的 ``read_class_comparison`` 服务，:func:`compare_formal_classes`
  按该节的键对齐规则做只读投影（同系列、同节点、同目录版本、按指标粒度对齐），
  正式服务落地后应替换本实现。
- 正式版本缺失时退化为读取该次运行的 ``analysis_results`` 行，并在 limitations 里注明
  数据来源，避免把未发布结果说成已发布。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.analysis import progress as progress_rules
from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    ClassComparisonView,
    ClassResult,
    ComparisonItem,
    DomainError,
    ProgressComparison,
    StudentResult,
)
from study_companion.db import models

#: 一次最多返回的比较条数，避免把整份指标表塞给模型。
MAX_COMPARISONS = 50


def _dataset(db: Session, dataset_id: str) -> models.Dataset:
    row = db.get(models.Dataset, dataset_id)
    if row is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    return row


def _load_version(db: Session, version_id: str) -> models.Version:
    row = db.get(models.Version, version_id)
    if row is None or row.payload_json is None:
        raise DomainError("NOT_FOUND", "正式版本不存在或内容不可读")
    return row


def _analysis_heads(
    db: Session, *, dataset_id: str, kind: str, node_key: str, target_key: str
) -> list[models.FormalHead]:
    """个人/班级分析版本头的 run_key 是 run_id，按作用域倒序取候选。"""

    return list(
        db.scalars(
            select(models.FormalHead)
            .where(
                models.FormalHead.dataset_id == dataset_id,
                models.FormalHead.kind == kind,
                models.FormalHead.node_key == node_key,
                models.FormalHead.target_key == target_key,
                models.FormalHead.version_id.is_not(None),
            )
            .order_by(models.FormalHead.updated_at.desc())
        ).all()
    )


def _latest_run_results(
    db: Session, *, dataset_id: str, run_id: str | None, subject_type: str, subject_id: str, node_id: str
) -> models.AnalysisResult | None:
    """正式版本缺失时的只读退化来源：该运行（或最近一次成功运行）的结果行。"""

    if run_id:
        row = db.get(models.AnalysisResult, (run_id, subject_type, subject_id, node_id))
        return row
    runs = db.scalars(
        select(models.AnalysisRun)
        .where(models.AnalysisRun.dataset_id == dataset_id, models.AnalysisRun.status == "succeeded")
        .order_by(models.AnalysisRun.created_at.desc())
    ).all()
    for run in runs:
        row = db.get(models.AnalysisResult, (run.id, subject_type, subject_id, node_id))
        if row is not None:
            return row
    return None


# --------------------------------------------------------------------------- #
# 学生进展（03 §10）
# --------------------------------------------------------------------------- #


def _previous_version_id(db: Session, version_id: str) -> str | None:
    """沿发布链取上一个正式版本；没有发布记录就没有基线。"""

    publication = db.scalar(
        select(models.Publication).where(models.Publication.version_id == version_id)
    )
    return publication.previous_version_id if publication is not None else None


def _student_result_from_version(
    db: Session, version: models.Version, *, dataset_id: str, student_id: str, node_id: str
) -> StudentResult:
    if version.kind != "student_analysis":
        raise DomainError("INVALID_ARGUMENT", "该版本不是学生分析版本")
    if version.dataset_id != dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "分析版本不属于当前数据集")
    if version.target_key != student_id:
        raise DomainError("FORBIDDEN", "该分析版本不属于此学生")
    if version.node_key and node_id and version.node_key != node_id:
        raise DomainError("CONTEXT_MISMATCH", "分析版本与请求节点不一致")
    try:
        return StudentResult.model_validate(version.payload_json or {})
    except Exception as exc:  # 旧版本口径不完整
        raise DomainError("RESULT_NOT_READY", "该分析版本的正式内容不可读") from exc


def compare_student_progress(
    db: Session,
    *,
    dataset_id: str,
    student_id: str,
    node_id: str,
    baseline_analysis_version: str | None = None,
    current_analysis_version: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """基于 B 正式分析版本比较两次结果。

    返回 ``comparable``、逐指标 ``comparisons`` 与 ``limitations``。没有基线版本时
    ``comparable=False`` 且 ``comparisons=[]``，由调用方如实说明"不可比"。
    """

    _dataset(db, dataset_id)
    limitations: list[str] = []
    source = ""
    persisted = False

    if current_analysis_version:
        current_version = _load_version(db, current_analysis_version)
        current = _student_result_from_version(
            db, current_version, dataset_id=dataset_id, student_id=student_id, node_id=node_id
        )
        current_version_id = current_version.version_id
        node_id = node_id or current_version.node_key
        source = "formal_version"
    else:
        heads = _analysis_heads(
            db, dataset_id=dataset_id, kind="student_analysis", node_key=node_id, target_key=student_id
        )
        if heads:
            current_version = _load_version(db, heads[0].version_id or "")
            current = _student_result_from_version(
                db, current_version, dataset_id=dataset_id, student_id=student_id, node_id=node_id
            )
            current_version_id = current_version.version_id
            source = "formal_version"
        else:
            row = _latest_run_results(
                db,
                dataset_id=dataset_id,
                run_id=None,
                subject_type="student",
                subject_id=student_id,
                node_id=node_id,
            )
            if row is None:
                raise DomainError("RESULT_NOT_READY", "该学生在该节点还没有正式分析结果")
            try:
                current = StudentResult.model_validate(row.payload_json or {})
            except Exception as exc:
                raise DomainError("RESULT_NOT_READY", "分析结果内容不可读") from exc
            current_version_id = None
            source = "analysis_result"
            limitations.append("读取的是分析运行结果，尚未发布为正式分析版本")

    baseline: StudentResult | None = None
    baseline_version_id: str | None = None
    if baseline_analysis_version:
        baseline_version = _load_version(db, baseline_analysis_version)
        baseline = _student_result_from_version(
            db, baseline_version, dataset_id=dataset_id, student_id=student_id, node_id=node_id
        )
        baseline_version_id = baseline_version.version_id
    elif current_version_id is not None:
        previous_id = _previous_version_id(db, current_version_id)
        if previous_id:
            baseline_version = _load_version(db, previous_id)
            baseline = _student_result_from_version(
                db, baseline_version, dataset_id=dataset_id, student_id=student_id, node_id=node_id
            )
            baseline_version_id = previous_id

    if baseline is None:
        return {
            "student_id": student_id,
            "node_id": node_id,
            "comparable": False,
            "source": source,
            "current_analysis_version": current.analysis_version or current_version_id or "",
            "baseline_analysis_version": None,
            "comparisons": [],
            "limitations": limitations
            + [
                "没有可比对的基线正式分析版本（首次分析或未登记发布链），"
                "因此不判断进步或退步，也不据数值差宣布改善",
            ],
        }

    # 正式结果里已持久化过 progress 时直接投影，不重复计算。
    if not baseline_analysis_version and current.progress:
        comparisons = list(current.progress)
        persisted = True
        source = f"{source}+persisted_progress"
    else:
        comparisons = progress_rules.compare_progress(baseline, current)
        limitations.append("进展比较由 B 的比较规则在查询时按正式版本只读重算，未写回正式版本")
        persisted = False

    if scope:
        comparisons = [
            item
            for item in comparisons
            if scope in {item.metric_name, item.scope.indicator_id, item.scope.kp_id}
        ]

    limited = comparisons[:MAX_COMPARISONS]
    if len(comparisons) > MAX_COMPARISONS:
        limitations.append(f"比较结果超过 {MAX_COMPARISONS} 项，只返回前 {MAX_COMPARISONS} 项")
    comparable = bool(limited) and any(item.comparable for item in limited)
    if limited and not comparable:
        limitations.append("所有比较项都不可比，不生成变化结论")
    return {
        "student_id": student_id,
        "node_id": node_id,
        "comparable": comparable,
        "source": source,
        "persisted": persisted,
        "current_analysis_version": current.analysis_version or current_version_id or "",
        "baseline_analysis_version": baseline.analysis_version or baseline_version_id or "",
        "comparisons": limited,
        "limitations": limitations,
    }


# --------------------------------------------------------------------------- #
# 班级横向比较（03 §12.4）
# --------------------------------------------------------------------------- #


def _class_result(
    db: Session, *, dataset_id: str, run_id: str, node_id: str, class_id: str
) -> tuple[ClassResult, str]:
    heads = _analysis_heads(
        db, dataset_id=dataset_id, kind="class_analysis", node_key=node_id, target_key=class_id
    )
    for head in heads:
        row = db.get(models.Version, head.version_id or "")
        if row is None or row.payload_json is None or row.run_id != run_id:
            continue
        try:
            return ClassResult.model_validate(row.payload_json), "formal_version"
        except Exception:  # 旧版本口径不完整时继续找运行结果
            break
    row = _latest_run_results(
        db, dataset_id=dataset_id, run_id=run_id, subject_type="class", subject_id=class_id, node_id=node_id
    )
    if row is None:
        raise DomainError("NOT_FOUND", f"班级 {class_id} 在该运行和节点没有分析结果")
    try:
        return ClassResult.model_validate(row.payload_json or {}), "analysis_result"
    except Exception as exc:
        raise DomainError("RESULT_NOT_READY", f"班级 {class_id} 的分析结果内容不可读") from exc


def compare_formal_classes(
    db: Session,
    *,
    dataset_id: str,
    run_id: str,
    node_id: str,
    class_ids: list[str],
    comparison_scope: str | None = None,
) -> ClassComparisonView:
    """按 B 的比较键对齐两个正式班级结果；缺项或口径不同只给限制，不强算。"""

    if len(class_ids) != 2 or len({*class_ids}) != 2:
        raise DomainError("INVALID_ARGUMENT", "compare_classes 需要两个不同的班级")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    if run.dataset_id != dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "分析运行不属于当前数据集")

    left, left_source = _class_result(
        db, dataset_id=dataset_id, run_id=run_id, node_id=node_id, class_id=class_ids[0]
    )
    right, right_source = _class_result(
        db, dataset_id=dataset_id, run_id=run_id, node_id=node_id, class_id=class_ids[1]
    )

    limitations: list[str] = []
    comparable = True
    if left.node_id != right.node_id or left.node_id != node_id:
        comparable = False
        limitations.append("两个班级的节点不一致，教学范围不可等同，不做比较")
    if left.catalog_version != right.catalog_version:
        comparable = False
        limitations.append(
            f"两个班级使用的目录版本不同（{left.catalog_version} / {right.catalog_version}），题目集合不可比"
        )
    if run.knowledge_version:
        limitations.append(f"本次比较基于知识版本 {run.knowledge_version}，跨版本结论不适用")
    limitations.append(
        f"{left.class_id}：名册 {left.roster_count} 人、有效观测 {left.observed_student_count} 人；"
        f"{right.class_id}：名册 {right.roster_count} 人、有效观测 {right.observed_student_count} 人"
    )
    if "analysis_result" in {left_source, right_source}:
        limitations.append("部分班级读取的是分析运行结果，尚未发布为正式班级分析版本")

    def keyed(result: ClassResult) -> dict[tuple[str, str, str], Any]:
        return {
            (metric.scope.indicator_id or "", metric.scope.kp_id or "", metric.unit): metric
            for metric in result.metrics
        }

    left_metrics = keyed(left)
    right_metrics = keyed(right)
    items: list[ComparisonItem] = []
    for key, metric in left_metrics.items():
        indicator_id, kp_id, _unit = key
        if comparison_scope and comparison_scope not in {indicator_id, kp_id}:
            continue
        other = right_metrics.get(key)
        if other is None:
            comparable = False
            limitations.append(f"右班缺少同口径指标（指标 {indicator_id}、知识点 {kp_id or '未细分'}）")
            continue
        delta = None
        # delta 只给同口径的 right − left，沿用正式单位；不汇总总分差（03 §12.4）。
        if metric.value is not None and other.value is not None:
            delta = round(other.value - metric.value, 6)
        items.append(
            ComparisonItem(
                indicator_id=indicator_id,
                left_value=metric.value,
                right_value=other.value,
                delta=delta,
                left_numerator=metric.numerator,
                left_denominator=metric.denominator,
                right_numerator=other.numerator,
                right_denominator=other.denominator,
                limitations=list(metric.limitations) + list(other.limitations),
            )
        )
    for key in right_metrics:
        if key in left_metrics:
            continue
        indicator_id, kp_id, _unit = key
        if comparison_scope and comparison_scope not in {indicator_id, kp_id}:
            continue
        comparable = False
        limitations.append(f"左班缺少同口径指标（指标 {indicator_id}、知识点 {kp_id or '未细分'}）")
    if not items:
        comparable = False
        limitations.append("两个班级没有可对齐的同口径指标，不给比较结论")

    evidence: list[dict[str, Any]] = []
    for key in left_metrics:
        metric = left_metrics[key]
        evidence.append(
            {
                "kind": "metric",
                "id": metric.metric_id,
                "run_id": run_id,
                "dataset_id": dataset_id,
            }
        )
    return ClassComparisonView(
        class_ids=list(class_ids),
        node_id=node_id,
        member_analysis_versions=[
            {"class_id": left.class_id, "analysis_version": left.analysis_version or "", "source": left_source},
            {"class_id": right.class_id, "analysis_version": right.analysis_version or "", "source": right_source},
        ],
        comparable=comparable,
        items=items,
        evidence_refs=evidence[:20],
        limitations=limitations,
    )


# --------------------------------------------------------------------------- #
# B 质量报告（03 §15）
# --------------------------------------------------------------------------- #


def b_quality_report(
    db: Session, *, dataset_id: str, run_id: str, node_id: str, generated_at: str
) -> Any:
    """读取正式记录并调用 B 的确定性聚合，生成可逐项核对的 B 质量报告。"""

    from study_companion.analysis.quality import build_quality_report
    from study_companion.library.datasets import load_catalog, load_prepared_dataset

    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    if run.dataset_id != dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "分析运行不属于当前数据集")
    if run.status != "succeeded":
        raise DomainError("RESULT_NOT_READY", "分析运行尚未成功完成")
    prepared = load_prepared_dataset(db, dataset_id)
    catalog = load_catalog(db, run.catalog_version, dataset_id)
    rows = db.scalars(
        select(models.AnalysisResult).where(models.AnalysisResult.run_id == run_id)
    ).all()
    bundle = AnalysisBundle(
        run_id=run_id,
        dataset_id=dataset_id,
        catalog_version=run.catalog_version,
        rule_version=run.rule_version,
        config=AnalysisConfig(**dict(run.config_json)),
        student_results=[
            row.payload_json for row in rows if row.subject_type == "student" and row.node_id == node_id
        ],
        class_results=[
            row.payload_json for row in rows if row.subject_type == "class" and row.node_id == node_id
        ],
        quality_summary={},
        knowledge_version=run.knowledge_version,
    )
    return build_quality_report(
        dataset_id=dataset_id,
        node_id=node_id,
        prepared=prepared,
        bundle=bundle,
        catalog=catalog,
        rule_version=run.rule_version,
        generated_at=generated_at,
    )


# --------------------------------------------------------------------------- #
# 供上层复用的引用工具
# --------------------------------------------------------------------------- #


def comparison_evidence_refs(comparisons: list[ProgressComparison], *, dataset_id: str) -> list[dict[str, Any]]:
    """把比较结果里的指标/记录引用整理成 EvidenceRef 字典（去重、保守截断）。"""

    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in comparisons:
        for ref in item.evidence_refs:
            payload = ref.model_dump(mode="json")
            payload.setdefault("dataset_id", dataset_id)
            key = ref.key()
            if key in seen:
                continue
            seen.add(key)
            refs.append(payload)
            if len(refs) >= 20:
                return refs
    return refs


__all__ = [
    "b_quality_report",
    "compare_formal_classes",
    "compare_student_progress",
    "comparison_evidence_refs",
]

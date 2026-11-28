"""C 的质量报告（04 §9.2）。

报告只统计**已发生的事实**：生成了多少方案、多少进了完整审核、多少发布、
多少因材料不足顺延、多少进了教师介入、多少走了规则降级。分母为 0 时给
"无数据"，不伪造百分比。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import CQualityIssue, CQualityReport, Page
from study_companion.db import models


@dataclass
class QualityWindow:
    start: datetime
    end: datetime

    @classmethod
    def from_dates(cls, date_from: str | None, date_to: str | None) -> "QualityWindow":
        end = _parse_date(date_to) or datetime.now(UTC)
        start = _parse_date(date_from) or (end - timedelta(days=30))
        if start >= end:
            raise ValueError("date_from 必须早于 date_to")
        return cls(start=start, end=end)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _in_window(value: str | None, window: QualityWindow) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return window.start <= stamp < window.end


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def build_report(
    db: Session,
    *,
    dataset_id: str,
    node_id: str,
    window: QualityWindow,
    class_id: str | None = None,
) -> CQualityReport:
    plan_versions = [
        row
        for row in db.scalars(
            select(models.Version).where(
                models.Version.dataset_id == dataset_id,
                models.Version.kind == "plan",
                models.Version.node_key == node_id,
            )
        ).all()
        if _in_window(row.created_at, window)
    ]
    if class_id:
        roster = {
            row.student_id
            for row in db.scalars(
                select(models.Student).where(
                    models.Student.dataset_id == dataset_id,
                    models.Student.class_id == class_id,
                )
            ).all()
        }
        plan_versions = [row for row in plan_versions if row.target_key in roster]

    published = 0
    human_confirmed = 0
    needs_review = 0
    shortages = 0
    rule_degraded = 0
    issues: list[CQualityIssue] = []

    for row in plan_versions:
        payload = dict(row.payload_json or {})
        if payload.get("publication_status") == "published":
            published += 1
            if payload.get("publish_path") == "teacher_direct":
                human_confirmed += 1
        review_info = payload.get("review_info") or {}
        if review_info.get("status") in {"needs_review", "draft"}:
            needs_review += 1
            issues.append(
                CQualityIssue(
                    issue_id=f"plan-review-{row.version_id}",
                    code="PLAN_NEEDS_REVIEW",
                    severity="warning",
                    student_id=row.target_key,
                    plan_version=row.version_id,
                    created_at=row.created_at,
                    action="等待教师处理或重新生成",
                )
            )
        for issue in payload.get("validation_issues", []) or []:
            if issue.get("code") == "MATERIAL_SHORTAGE":
                shortages += 1
                issues.append(
                    CQualityIssue(
                        issue_id=f"shortage-{row.version_id}",
                        code="MATERIAL_SHORTAGE",
                        severity="warning",
                        student_id=row.target_key,
                        plan_version=row.version_id,
                        created_at=row.created_at,
                        action="补材料或调整目标",
                    )
                )
                break
        if review_info.get("method") == "rule" and payload.get("generation_method") == "rule":
            rule_degraded += 1

    kept_old = db.scalars(
        select(models.PlanApplicability).where(
            models.PlanApplicability.result == "applicable"
        )
    ).all()
    kept_old = [row for row in kept_old if _in_window(row.checked_at, window)]

    trials = db.scalars(select(models.StrategyTrialRow)).all()
    trials = [row for row in trials if _in_window(row.created_at, window)]

    interventions = db.scalars(select(models.InterventionRow)).all()
    opened = [row for row in interventions if _in_window(row.opened_at, window)]
    resolved = [row for row in interventions if _in_window(row.resolved_at, window)]
    open_at_end = [
        row
        for row in interventions
        if row.status == "open"
        and row.opened_at
        and row.opened_at < window.end.isoformat()
    ]

    paused = db.scalars(
        select(models.ActivityStateRow).where(
            models.ActivityStateRow.suspension_reason.is_not(None)
        )
    ).all()

    jobs = db.scalars(select(models.Job).where(models.Job.dataset_id == dataset_id)).all()
    jobs = [row for row in jobs if _in_window(row.created_at, window)]
    failures = [row for row in jobs if row.status == "failed"]

    counts = {
        "plans_generated": len(plan_versions),
        "plans_published": published,
        "plans_human_confirmed": human_confirmed,
        "plans_needs_review": needs_review,
        "plans_kept_applicable": len(kept_old),
        "material_shortage": shortages,
        "strategy_trials": len(trials),
        "interventions_opened": len(opened),
        "interventions_resolved": len(resolved),
        "interventions_open_at_end": len(open_at_end),
        "activities_paused": len(paused),
        "rule_degraded": rule_degraded,
        "jobs_failed": len(failures),
    }

    coverage = [
        {
            "key": "publication_rate",
            "label": "方案发布率",
            "numerator": published,
            "denominator": len(plan_versions),
            "value": _ratio(published, len(plan_versions)),
            "scope_description": "窗口内发布的方案版本／生成的方案版本",
        },
        {
            "key": "review_pass_rate",
            "label": "首次审核通过率",
            "numerator": len(plan_versions) - needs_review,
            "denominator": len(plan_versions),
            "value": _ratio(len(plan_versions) - needs_review, len(plan_versions)),
            "scope_description": "窗口内通过审核的方案版本／生成的方案版本",
        },
        {
            "key": "intervention_resolution_rate",
            "label": "介入结案率",
            "numerator": len(resolved),
            "denominator": len(opened),
            "value": _ratio(len(resolved), len(opened)),
            "scope_description": "窗口内结案的介入案例／窗口内开启的案例",
        },
    ]

    limitations = [
        "计数按方案版本、介入案例与任务分别去重，重试不重复计数",
        "分母为 0 时不给百分比，显示为无数据",
    ]
    if rule_degraded:
        limitations.append("部分方案由规则模板生成（模型未参与），已计入 rule_degraded")

    return CQualityReport(
        scope={"dataset_id": dataset_id, "node_id": node_id, "class_id": class_id},
        window={"from": window.start.isoformat(), "to": window.end.isoformat()},
        generated_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
        rule_version="c-quality-v1",
        counts=counts,
        coverage=coverage,
        issues={
            "items": [item.model_dump(mode="json") for item in issues],
            "total": len(issues),
            "page": 1,
            "page_size": 50,
        },
        limitations=limitations,
    )

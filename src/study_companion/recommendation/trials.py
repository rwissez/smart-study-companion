"""策略试验与切换判断（04 §7）。

一次"试验"= 一个任务组（目标 + 主策略 + 素材指纹）。记录它是为了两件事：

1. 可比且未改善时，避免无限重复同一套安排；
2. 两轮无效就换策略，三轮有效教学仍无改善则开教师介入。

判定只用 B 的进展分类，C 不另造"有效"的标准。
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models

EFFECTIVE_CLASSIFICATIONS = {
    "improved": "effective",
    "partially_improved": "partial",
    "unchanged_or_declined": "ineffective",
    "insufficient": "inconclusive",
}

#: 同一策略连续无效多少次后换策略；两轮有效教学无改善则开教师介入。
SWITCH_AFTER_INVALID = 2
ESCALATE_AFTER_ROUNDS = 3

FALLBACK_STRATEGY = {
    "error_correction": "prerequisite_support",
    "consolidation": "collect_evidence",
    "transfer": "consolidation",
    "prerequisite_support": "collect_evidence",
}


def action_fingerprint(strategy: str, material_ids: list[str]) -> str:
    payload = "|".join([strategy, *sorted(material_ids)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def record_trials(
    db: Session,
    *,
    plan_version: str,
    groups: list[dict[str, Any]],
    analysis_version: str,
    created_at: str,
) -> list[models.StrategyTrialRow]:
    rows: list[models.StrategyTrialRow] = []
    for group in groups:
        fingerprint = action_fingerprint(group["strategy"], group["material_ids"])
        existing = db.scalar(
            select(models.StrategyTrialRow).where(
                models.StrategyTrialRow.plan_version == plan_version,
                models.StrategyTrialRow.group_id == group["group_id"],
                models.StrategyTrialRow.target_key == group["target_key"],
                models.StrategyTrialRow.strategy == group["strategy"],
            )
        )
        if existing is not None:
            rows.append(existing)
            continue
        row = models.StrategyTrialRow(
            trial_id=f"trial-{hashlib.sha256((plan_version + group['group_id']).encode()).hexdigest()[:16]}",
            plan_version=plan_version,
            group_id=group["group_id"],
            target_key=group["target_key"],
            strategy=group["strategy"],
            comparison_id=None,
            payload_json={
                "action_fingerprint": fingerprint,
                "baseline_analysis_version": analysis_version,
                "material_ids": group["material_ids"],
            },
            status="running",
            created_at=created_at,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def update_trials_from_progress(
    db: Session,
    *,
    plan_version: str,
    comparisons: list[dict[str, Any]],
    now_iso: str,
) -> list[dict[str, Any]]:
    """用进展分类更新试验状态；返回本轮的变化（供审计/日志）。"""

    trials = db.scalars(
        select(models.StrategyTrialRow).where(models.StrategyTrialRow.plan_version == plan_version)
    ).all()
    by_indicator = {
        (item.get("scope") or {}).get("indicator_id"): item for item in comparisons
    }
    changes: list[dict[str, Any]] = []
    for trial in trials:
        if trial.status in {"effective", "ineffective", "partial"}:
            continue
        comparison = by_indicator.get(trial.target_key)
        if comparison is None:
            continue
        status = EFFECTIVE_CLASSIFICATIONS.get(comparison.get("classification"), "inconclusive")
        if status == "inconclusive":
            continue
        trial.status = status
        trial.comparison_id = comparison.get("comparison_id")
        payload = dict(trial.payload_json or {})
        payload["last_classification"] = comparison.get("classification")
        trial.payload_json = payload
        changes.append(
            {
                "trial_id": trial.trial_id,
                "target_key": trial.target_key,
                "strategy": trial.strategy,
                "status": status,
            }
        )
    db.flush()
    return changes


def suppression_for(
    db: Session, *, target_key: str, current_fingerprint: str | None = None
) -> dict[str, Any]:
    """返回该目标应被抑制的策略，以及是否需要开教师介入。"""

    trials = db.scalars(
        select(models.StrategyTrialRow).where(models.StrategyTrialRow.target_key == target_key)
    ).all()
    by_strategy: dict[str, list[models.StrategyTrialRow]] = {}
    for trial in trials:
        by_strategy.setdefault(trial.strategy, []).append(trial)

    suppressed: set[str] = set()
    escalate = False
    for strategy, items in by_strategy.items():
        ordered = sorted(items, key=lambda row: row.created_at)
        recent = ordered[-SWITCH_AFTER_INVALID:]
        if len(recent) == SWITCH_AFTER_INVALID and all(item.status == "ineffective" for item in recent):
            suppressed.add(strategy)
        if len(ordered) >= ESCALATE_AFTER_ROUNDS and all(
            item.status in {"ineffective", "inconclusive"} for item in ordered
        ):
            escalate = True
            suppressed.add(strategy)
    return {"suppressed": suppressed, "escalate": escalate}


def open_intervention_for(
    db: Session,
    *,
    dataset_id: str,
    student_uid: str,
    student_id: str,
    target_key: str,
    reason_code: str,
    summary: str,
    evidence_refs: list[dict[str, Any]],
    now_iso: str,
) -> models.InterventionRow | None:
    """三轮仍无改善时开教师介入；同 scope/目标/原因只保留一个开放案例。"""

    scope_key = f"{dataset_id}:{target_key}"
    existing = db.scalar(
        select(models.InterventionRow).where(
            models.InterventionRow.scope_key == scope_key,
            models.InterventionRow.target_key == target_key,
            models.InterventionRow.reason_code == reason_code,
            models.InterventionRow.status == "open",
        )
    )
    if existing is not None:
        return existing
    row = models.InterventionRow(
        case_id=f"case-{hashlib.sha256((scope_key + reason_code).encode()).hexdigest()[:16]}",
        student_uid=student_uid,
        scope_key=scope_key,
        target_key=target_key,
        reason_code=reason_code,
        payload_json={"reason": summary, "evidence_refs": evidence_refs, "student_id": student_id},
        status="open",
        last_cutoff=0,
        opened_at=now_iso,
    )
    db.add(row)
    db.flush()
    return row

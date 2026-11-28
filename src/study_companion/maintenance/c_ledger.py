"""C 账本一致性维护：补任务组查找行、核对微课堂来源标签。

背景（S1-E）：``pipeline.attach_lessons`` 生成微课堂子修订时只写
``plan_activities``，没有写 ``plan_groups``；当前正式方案因此在派生表里
“有活动、无任务组”。修历史数据只能补派生表，不能改已发布版本：

* 版本 ``payload_json`` 与 ``body_hash`` 是证据，工具只读不写；
* 正式指针 ``formal_heads`` / ``publications`` 一律不动；
* ``plan_activities`` 没有 ``source_label`` 列，标签只存在于方案载荷里。
  历史标签不一致只能报告，不能在不动载荷的前提下修正；未来写入由
  ``pipeline.attach_lessons`` 按 ``creation_method`` 直接写对。

CLI（默认演练，不写库）::

    python -m study_companion.maintenance.c_ledger --db data/app.sqlite3
    python -m study_companion.maintenance.c_ledger --db backup/app.sqlite3 --apply

``--apply`` 只提交缺失的 ``plan_groups`` 行；重复运行不会产生重复行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from study_companion.db import models


def _current_plan_versions(db: Session, dataset_id: str | None = None) -> list[models.Version]:
    """按正式指针取当前方案版本；只读，不解析历史方案。"""

    statement = select(models.FormalHead).where(
        models.FormalHead.kind == "plan",
        models.FormalHead.version_id.is_not(None),
    )
    if dataset_id:
        statement = statement.where(models.FormalHead.dataset_id == dataset_id)
    versions: list[models.Version] = []
    for head in db.scalars(statement.order_by(models.FormalHead.dataset_id)).all():
        row = db.get(models.Version, head.version_id)
        if row is not None and row.payload_json is not None:
            versions.append(row)
    return versions


def _payload_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("task_groups") or []
    return [group for group in groups if isinstance(group, dict)]


def backfill_plan_groups(
    db: Session,
    *,
    dataset_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """给当前正式方案补齐缺失的 ``plan_groups`` 行（幂等）。

    只读方案载荷里的 ``task_groups``，按 ``group_id`` 建行，``position`` 取
    载荷 ``order``（缺失时按列表顺序），``goal_key`` 取 ``goal_target_key``。
    已有行一律保留，不重排、不改写，因此重复运行只会在首次插入缺失行。
    """

    report: dict[str, Any] = {
        "dataset_id": dataset_id or "",
        "dry_run": dry_run,
        "checked_plans": 0,
        "plans_with_all_groups": 0,
        "plans_missing_groups": 0,
        "groups_in_payload": 0,
        "groups_existing_before": 0,
        "groups_inserted": 0,
        "groups_still_missing": 0,
        "skipped": [],
        "plans": [],
    }
    for version in _current_plan_versions(db, dataset_id):
        report["checked_plans"] += 1
        payload = version.payload_json or {}
        groups = _payload_groups(payload)
        existing = {
            row.group_id: row
            for row in db.scalars(
                select(models.PlanGroup).where(models.PlanGroup.plan_version == version.version_id)
            ).all()
        }
        existing_before = len(existing)
        known_group_ids = set(existing)
        occupied_positions = {row.position for row in existing.values()}
        inserted = 0
        missing = 0
        report["groups_in_payload"] += len(groups)
        report["groups_existing_before"] += existing_before
        for index, group in enumerate(groups, start=1):
            group_id = str(group.get("group_id") or "").strip()
            if not group_id:
                report["skipped"].append(
                    {
                        "plan_version": version.version_id,
                        "group_id": "",
                        "reason": "payload 任务组缺少 group_id",
                    }
                )
                continue
            if group_id in known_group_ids:
                continue
            position = group.get("order")
            if not isinstance(position, int) or position < 1:
                position = index
            if position in occupied_positions:
                report["skipped"].append(
                    {
                        "plan_version": version.version_id,
                        "group_id": group_id,
                        "reason": f"position {position} 已被其他任务组占用",
                    }
                )
                missing += 1
                continue
            if not dry_run:
                db.add(
                    models.PlanGroup(
                        plan_version=version.version_id,
                        group_id=group_id,
                        position=position,
                        goal_key=str(group.get("goal_target_key") or group_id),
                    )
                )
            occupied_positions.add(position)
            known_group_ids.add(group_id)
            inserted += 1
        report["groups_inserted"] += inserted
        report["groups_still_missing"] += missing
        if inserted or missing:
            report["plans_missing_groups"] += 1
        else:
            report["plans_with_all_groups"] += 1
        report["plans"].append(
            {
                "plan_version": version.version_id,
                "student_id": version.target_key,
                "node_id": version.node_key,
                "groups_in_payload": len(groups),
                "groups_existing_before": existing_before,
                "groups_inserted": inserted,
                "groups_still_missing": missing,
            }
        )
    if not dry_run:
        db.flush()
    return report


def activity_label_report(
    db: Session,
    *,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """报告当前方案里与 ``content_version.creation_method`` 不一致的来源标签。

    不写任何行。历史方案载荷是已发布证据，标签错误只作为未闭环限制列出。
    """

    from study_companion.pipeline import lesson_activity_source_label

    report: dict[str, Any] = {
        "dataset_id": dataset_id or "",
        "checked_plans": 0,
        "checked_labeled_activities": 0,
        "mismatches": 0,
        "model_mismatches": 0,
        "rule_mismatches": 0,
        "unknown_creation_method": [],
        "issues": [],
    }
    for version in _current_plan_versions(db, dataset_id):
        report["checked_plans"] += 1
        payload = version.payload_json or {}
        for recommendation in payload.get("recommendations") or []:
            if not isinstance(recommendation, dict):
                continue
            for activity in recommendation.get("activities") or []:
                if not isinstance(activity, dict):
                    continue
                if activity.get("activity_kind") != "micro_lesson":
                    continue
                content_version = str(activity.get("content_version") or "").strip()
                if not content_version:
                    continue
                report["checked_labeled_activities"] += 1
                lesson = db.get(models.Version, content_version)
                lesson_payload = (lesson.payload_json or {}) if lesson is not None else {}
                creation_method = str(lesson_payload.get("creation_method") or "").strip()
                if creation_method not in {"model", "rule"}:
                    report["unknown_creation_method"].append(
                        {
                            "plan_version": version.version_id,
                            "activity_id": activity.get("activity_id"),
                            "content_version": content_version,
                            "creation_method": creation_method,
                        }
                    )
                    continue
                expected = lesson_activity_source_label(creation_method)
                current = activity.get("source_label")
                if current == expected:
                    continue
                report["mismatches"] += 1
                if creation_method == "model":
                    report["model_mismatches"] += 1
                else:
                    report["rule_mismatches"] += 1
                report["issues"].append(
                    {
                        "student_id": version.target_key,
                        "node_id": version.node_key,
                        "plan_version": version.version_id,
                        "activity_id": activity.get("activity_id"),
                        "content_version": content_version,
                        "creation_method": creation_method,
                        "current_label": current,
                        "expected_label": expected,
                    }
                )
    return report


def backfill(
    db: Session,
    *,
    dataset_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行补组并附带标签核对报告；调用方负责提交或回滚。"""

    return {
        "plan_groups": backfill_plan_groups(db, dataset_id=dataset_id, dry_run=dry_run),
        "activity_labels": activity_label_report(db, dataset_id=dataset_id),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m study_companion.maintenance.c_ledger",
        description="补当前正式方案的 plan_groups，并核对微课堂来源标签。",
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="SQLite 数据库路径；请传备份或临时副本，不要直接对正式库演练。",
    )
    parser.add_argument("--dataset-id", default=None, help="只处理指定数据集快照")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="提交缺失的 plan_groups 行；缺省只演练并输出报告",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="把 JSON 报告另存到指定文件（UTF-8），便于留证与复核",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path: Path = args.db.expanduser().resolve()
    if not path.is_file():
        print(f"数据库不存在：{path}", file=sys.stderr)
        return 2

    from study_companion.config import Settings
    from study_companion.db.engine import build_engine

    engine = build_engine(Settings(database_url=f"sqlite:///{path.as_posix()}"))
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        result = backfill(
            session,
            dataset_id=args.dataset_id,
            dry_run=not args.apply,
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()
        engine.dispose()

    result["applied"] = bool(args.apply)
    result["db_path"] = str(path)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out is not None:
        out_path = args.out.expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())

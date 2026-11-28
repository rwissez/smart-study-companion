"""C 账本回填：只补派生表，幂等，不碰已发布版本与发布指针。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import Activity, Recommendation, RecommendationBundle, TaskGroup
from study_companion.db import models, version_repository as repo
from study_companion.maintenance import c_ledger
from study_companion.pipeline import LESSON_SOURCE_LABELS


def _foundation(session: Session) -> tuple[str, str]:
    token = uuid.uuid4().hex[:10]
    family_id = f"family-{token}"
    dataset_id = f"ds-{token}"
    session.add(
        models.DatasetFamily(
            family_id=family_id, label="测试", data_origin="synthetic", created_at=repo.now_iso()
        )
    )
    session.flush()
    session.add(
        models.Dataset(
            id=dataset_id,
            created_at=repo.now_iso(),
            parent_id=None,
            status="ready",
            manifest_json={"synthetic": True},
            family_id=family_id,
            state="ready",
        )
    )
    session.flush()
    return family_id, dataset_id


def _lesson_version(
    session: Session,
    family_id: str,
    dataset_id: str,
    content_version: str,
    creation_method: str,
) -> None:
    repo.create_version(
        session,
        kind="lesson",
        key=repo.FormalKey(
            family_id=family_id,
            dataset_id=dataset_id,
            kind="lesson",
            target_key=content_version,
        ),
        payload={"creation_method": creation_method, "content_version": content_version},
        version_id=content_version,
    )


def _plan_with_lessons(
    session: Session,
    family_id: str,
    dataset_id: str,
    *,
    plan_version: str,
    student_id: str,
    node_id: str,
    lessons: list[tuple[str, str]],
    existing_group: bool = False,
) -> RecommendationBundle:
    groups = []
    recommendations = []
    activities = []
    for index, (group_id, content_version) in enumerate(lessons, start=1):
        activity = Activity(
            order=index,
            activity_id=f"act-{plan_version}-{index}",
            group_id=group_id,
            activity_kind="micro_lesson",
            content_version=content_version,
            content_ref={
                "kind": "personalized",
                "content_id": f"lesson-{index}",
                "content_version": content_version,
            },
            source_label=LESSON_SOURCE_LABELS["rule"],
            title=f"微课堂 {index}",
        )
        activities.append(activity)
        groups.append(
            TaskGroup(
                group_id=group_id,
                goal=f"目标 {index}",
                order=index,
                activity_ids=[activity.activity_id],
                required_activity_ids=[],
                goal_target_key=f"goal-{group_id}",
            )
        )
        recommendations.append(
            Recommendation(
                recommendation_id=f"rec-{index}",
                indicator_id=f"ind-{index}",
                finding_ids=[],
                goal=f"目标 {index}",
                reason="证据支持",
                activities=[activity],
                adjustment_conditions=[],
                group_id=group_id,
                kp_ids=[f"kp-{index}"],
            )
        )
    bundle = RecommendationBundle(
        run_id="run-test",
        dataset_id=dataset_id,
        catalog_version="cat-test",
        student_id=student_id,
        node_id=node_id,
        recommendations=recommendations,
        validation_issues=[],
        task_groups=groups,
        publication_status="published",
    )
    key = repo.plan_key(family_id, dataset_id, node_id, student_id)
    version = repo.create_version(
        session,
        kind="plan",
        key=key,
        payload=bundle.model_dump(mode="json"),
        version_id=plan_version,
    )
    repo.publish(session, key=key, version=version, path="system_auto", basis="automatic_review")
    if existing_group:
        session.add(
            models.PlanGroup(
                plan_version=plan_version,
                group_id=groups[0].group_id,
                position=groups[0].order,
                goal_key=groups[0].goal_target_key or groups[0].group_id,
            )
        )
        session.flush()
    return bundle


def _snapshot(session: Session) -> dict:
    session.expire_all()
    versions = {}
    for row in session.scalars(select(models.Version).order_by(models.Version.version_id)).all():
        versions[row.version_id] = {
            "body_hash": row.body_hash,
            "payload": json.dumps(row.payload_json, ensure_ascii=False, sort_keys=True),
        }
    heads = {
        (row.family_id, row.dataset_id, row.kind, row.run_key, row.node_key, row.target_key): (
            row.version_id,
            row.generation,
        )
        for row in session.scalars(select(models.FormalHead)).all()
    }
    publications = {
        row.publication_id: (row.version_id, row.previous_version_id, row.published_at)
        for row in session.scalars(select(models.Publication)).all()
    }
    plan_activities = {
        (row.plan_version, row.activity_id): (row.group_id, row.position, row.kind)
        for row in session.scalars(select(models.PlanActivity)).all()
    }
    return {
        "versions": versions,
        "heads": heads,
        "publications": publications,
        "plan_activities": plan_activities,
    }


def test_backfill_adds_only_missing_groups_and_is_idempotent(session: Session) -> None:
    family_id, dataset_id = _foundation(session)
    _lesson_version(session, family_id, dataset_id, "lessonv-model", "model")
    _lesson_version(session, family_id, dataset_id, "lessonv-rule", "rule")
    _plan_with_lessons(
        session,
        family_id,
        dataset_id,
        plan_version="plan-model",
        student_id="S01",
        node_id="N01",
        lessons=[("grp-1", "lessonv-model"), ("grp-2", "lessonv-rule")],
    )
    _plan_with_lessons(
        session,
        family_id,
        dataset_id,
        plan_version="plan-rule",
        student_id="S02",
        node_id="N01",
        lessons=[("grp-3", "lessonv-rule")],
        existing_group=True,
    )
    # 一个活动查找行，用来证明回填不碰活动表。
    session.add(
        models.PlanActivity(
            plan_version="plan-model",
            activity_id="act-manual",
            group_id="grp-1",
            kind="question",
            position=9,
        )
    )
    session.commit()
    before = _snapshot(session)

    dry = c_ledger.backfill_plan_groups(session, dry_run=True)
    assert dry["checked_plans"] == 2
    assert dry["groups_in_payload"] == 3
    assert dry["groups_existing_before"] == 1
    assert dry["groups_inserted"] == 2
    assert dry["groups_still_missing"] == 0
    session.rollback()
    assert _snapshot(session) == before

    applied = c_ledger.backfill_plan_groups(session, dry_run=False)
    assert applied["groups_inserted"] == 2
    session.commit()

    rows = {
        (row.plan_version, row.group_id): (row.position, row.goal_key)
        for row in session.scalars(select(models.PlanGroup)).all()
    }
    assert rows[("plan-model", "grp-1")] == (1, "goal-grp-1")
    assert rows[("plan-model", "grp-2")] == (2, "goal-grp-2")
    assert rows[("plan-rule", "grp-3")] == (1, "goal-grp-3")
    after = _snapshot(session)
    assert after["versions"] == before["versions"]
    assert after["heads"] == before["heads"]
    assert after["publications"] == before["publications"]
    assert after["plan_activities"] == before["plan_activities"]

    again = c_ledger.backfill_plan_groups(session, dry_run=False)
    assert again["groups_inserted"] == 0
    session.commit()
    assert session.scalar(select(models.PlanGroup).where(models.PlanGroup.plan_version == "plan-model")) is not None
    count = len(session.scalars(select(models.PlanGroup)).all())
    assert count == 3
    assert _snapshot(session)["versions"] == before["versions"]


def test_activity_label_report_flags_model_mismatch_only(session: Session) -> None:
    family_id, dataset_id = _foundation(session)
    _lesson_version(session, family_id, dataset_id, "lessonv-model", "model")
    _lesson_version(session, family_id, dataset_id, "lessonv-rule", "rule")
    _plan_with_lessons(
        session,
        family_id,
        dataset_id,
        plan_version="plan-model",
        student_id="S01",
        node_id="N01",
        lessons=[("grp-1", "lessonv-model"), ("grp-2", "lessonv-rule")],
    )
    session.commit()
    before = _snapshot(session)
    report = c_ledger.activity_label_report(session)
    session.rollback()

    assert report["checked_plans"] == 1
    assert report["checked_labeled_activities"] == 2
    assert report["mismatches"] == 1
    assert report["model_mismatches"] == 1
    assert report["rule_mismatches"] == 0
    issue = report["issues"][0]
    assert issue["student_id"] == "S01"
    assert issue["node_id"] == "N01"
    assert issue["plan_version"] == "plan-model"
    assert issue["content_version"] == "lessonv-model"
    assert issue["creation_method"] == "model"
    assert issue["current_label"] == LESSON_SOURCE_LABELS["rule"]
    assert issue["expected_label"] == LESSON_SOURCE_LABELS["model"]
    assert _snapshot(session) == before


def test_backfill_combines_groups_and_label_report(session: Session) -> None:
    family_id, dataset_id = _foundation(session)
    _lesson_version(session, family_id, dataset_id, "lessonv-model", "model")
    _plan_with_lessons(
        session,
        family_id,
        dataset_id,
        plan_version="plan-model",
        student_id="S01",
        node_id="N01",
        lessons=[("grp-1", "lessonv-model")],
    )
    result = c_ledger.backfill(session, dry_run=False)
    assert result["plan_groups"]["groups_inserted"] == 1
    assert result["activity_labels"]["mismatches"] == 1
    assert result["activity_labels"]["issues"][0]["expected_label"] == LESSON_SOURCE_LABELS["model"]

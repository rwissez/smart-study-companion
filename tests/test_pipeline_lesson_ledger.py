"""C 微课堂子修订的派生账本：任务组行与来源标签必须同步落库。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    Activity,
    Recommendation,
    RecommendationBundle,
    TaskGroup,
)
from study_companion.db import models, version_repository as repo
from study_companion.pipeline import (
    LESSON_SOURCE_LABELS,
    attach_lessons,
    lesson_activity_source_label,
)


def _foundation(session: Session, suffix: str | None = None) -> tuple[str, str]:
    token = suffix or uuid.uuid4().hex[:10]
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
    session.add(
        models.Catalog(
            version="cat-test",
            dataset_id=dataset_id,
            content_hash="",
            created_at=repo.now_iso(),
            payload_json={},
        )
    )
    session.flush()
    session.add(
        models.AnalysisRun(
            id="run-test",
            dataset_id=dataset_id,
            catalog_version="cat-test",
            rule_version="b-rules-1",
            config_json={},
            status="succeeded",
            created_at=repo.now_iso(),
            node_ids_json=[],
        )
    )
    session.flush()
    return family_id, dataset_id


def _plan_bundle(dataset_id: str, *, student_id: str = "S01") -> RecommendationBundle:
    activity = Activity(
        order=1,
        activity_id="act-q-1",
        group_id="grp-1",
        activity_kind="question",
        material_id="material-1",
        title="练习题",
    )
    recommendation = Recommendation(
        recommendation_id="rec-1",
        indicator_id="ind-1",
        finding_ids=[],
        goal="掌握有理数加法",
        reason="证据支持",
        activities=[activity],
        adjustment_conditions=[],
        group_id="grp-1",
        kp_ids=["kp-1"],
    )
    group = TaskGroup(
        group_id="grp-1",
        goal="掌握有理数加法",
        order=1,
        activity_ids=["act-q-1"],
        required_activity_ids=["act-q-1"],
        goal_target_key="ind-1",
        expected_outcome="能完成同类题",
    )
    return RecommendationBundle(
        run_id="run-test",
        dataset_id=dataset_id,
        catalog_version="cat-test",
        student_id=student_id,
        node_id="N01",
        recommendations=[recommendation],
        validation_issues=[],
        task_groups=[group],
    )


def _publish_plan(session: Session, family_id: str, dataset_id: str, bundle: RecommendationBundle):
    key = repo.plan_key(family_id, dataset_id, bundle.node_id, bundle.student_id)
    version = repo.create_version(
        session,
        kind="plan",
        key=key,
        payload=bundle.model_dump(mode="json"),
        version_id="plan-parent",
    )
    repo.publish(
        session,
        key=key,
        version=version,
        path="system_auto",
        basis="automatic_review",
    )
    return version


def test_future_rule_lesson_writes_groups_and_rule_label(session: Session) -> None:
    family_id, dataset_id = _foundation(session)
    bundle = _plan_bundle(dataset_id)
    _publish_plan(session, family_id, dataset_id, bundle)

    result = attach_lessons(
        session,
        family_id=family_id,
        dataset_id=dataset_id,
        student_id="S01",
        node_id="N01",
        plan_version="plan-parent",
        knowledge_payload={
            "points": [
                {
                    "kp_id": "kp-1",
                    "name": "有理数加法",
                    "definition": "同号相加取相同符号",
                    "indicator_ids": ["ind-1"],
                }
            ]
        },
        job_id="job-rule-lesson",
        student_uid=None,
    )
    assert result is not None
    child_id = result["plan_version"]

    groups = session.scalars(
        select(models.PlanGroup).where(models.PlanGroup.plan_version == child_id)
    ).all()
    assert len(groups) == 1
    assert (groups[0].group_id, groups[0].position, groups[0].goal_key) == ("grp-1", 1, "ind-1")

    child = session.get(models.Version, child_id)
    assert child is not None and child.payload_json is not None
    lesson_activities = [
        activity
        for rec in child.payload_json["recommendations"]
        for activity in rec["activities"]
        if activity["activity_kind"] == "micro_lesson"
    ]
    assert len(lesson_activities) == 1
    activity = lesson_activities[0]
    assert activity["source_label"] == LESSON_SOURCE_LABELS["rule"]

    lesson = session.get(models.Version, activity["content_version"])
    assert lesson is not None and lesson.payload_json is not None
    assert lesson.payload_json["creation_method"] == "rule"

    # 查找行与载荷一一对应，活动行也照旧落库。
    activity_rows = session.scalars(
        select(models.PlanActivity).where(models.PlanActivity.plan_version == child_id)
    ).all()
    assert {row.group_id for row in activity_rows} == {"grp-1"}
    assert len(activity_rows) == len(child.payload_json["recommendations"][0]["activities"])


def test_future_model_lesson_uses_model_label(session: Session, monkeypatch) -> None:
    """模型确实产出草稿时，标签才写模型文案；规则路径仍写规则文案。"""

    family_id, dataset_id = _foundation(session)
    bundle = _plan_bundle(dataset_id)
    _publish_plan(session, family_id, dataset_id, bundle)

    from study_companion.recommendation import lesson as lesson_service
    from study_companion.recommendation import lesson_model

    class _FakeModelService:
        configured = True

    def fake_generate_with_model(**kwargs):
        draft = lesson_service.build_rule_lesson(
            title=kwargs["title"],
            goal=kwargs["goal"],
            kp_id=kwargs["kp_id"],
            evidence_refs=kwargs["evidence_refs"],
            teaching_points=kwargs["teaching_points"],
            fallback_text=kwargs["goal"],
            student_error=kwargs["student_error"],
            error_cause=kwargs["error_cause"],
            material_ids=kwargs["material_ids"],
            student_id=kwargs["student_id"],
        )
        return draft, []

    monkeypatch.setattr(
        "study_companion.model_service.ModelService", lambda: _FakeModelService()
    )
    monkeypatch.setattr(lesson_model, "generate_with_model", fake_generate_with_model)

    result = attach_lessons(
        session,
        family_id=family_id,
        dataset_id=dataset_id,
        student_id="S01",
        node_id="N01",
        plan_version="plan-parent",
        knowledge_payload={
            "points": [
                {
                    "kp_id": "kp-1",
                    "name": "有理数加法",
                    "definition": "同号相加取相同符号",
                    "indicator_ids": ["ind-1"],
                }
            ]
        },
        job_id="job-model-lesson",
        student_uid=None,
    )
    assert result is not None
    child = session.get(models.Version, result["plan_version"])
    assert child is not None and child.payload_json is not None
    activity = next(
        activity
        for rec in child.payload_json["recommendations"]
        for activity in rec["activities"]
        if activity["activity_kind"] == "micro_lesson"
    )
    assert activity["source_label"] == LESSON_SOURCE_LABELS["model"]
    lesson = session.get(models.Version, activity["content_version"])
    assert lesson is not None and lesson.payload_json is not None
    assert lesson.payload_json["creation_method"] == "model"


def test_label_helper_does_not_fake_model_marker() -> None:
    assert lesson_activity_source_label("model") == "模型生成，已通过八项复核"
    assert lesson_activity_source_label("rule") == "规则模板生成，已审核"
    assert lesson_activity_source_label("hybrid") == "规则模板生成，已审核"
    assert lesson_activity_source_label(None) == "规则模板生成，已审核"

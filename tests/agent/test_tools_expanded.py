"""05 §6 只读扩展工具：权限、不可比、只读理由、学生裁剪与候选无副作用。"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from study_companion.agent.tasks import get_task
from study_companion.agent.tools import build_b_registry
from study_companion.agent.types import ToolCall
from study_companion.contracts import DomainError, RecommendationBundle
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.recommendation.publication import scope_key_for
from study_companion.tools.b_analysis import ServerContext

DATASET = "synthetic-ds"
CATALOG = "synthetic-v1"
RUN = "synthetic-run"
NODE = "N01"
PLAN = "plan-v1"
LESSON = "lesson-v1"


# --------------------------------------------------------------------------- #
# 夹具：在已 seed 的合成数据上补正式方案、微课堂与第二个班级结果
# --------------------------------------------------------------------------- #


def _family(db, dataset_id: str) -> str:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset.family_id:
        return dataset.family_id
    family_id = "fam-test"
    db.add(
        models.DatasetFamily(
            family_id=family_id,
            label="合成测试系列",
            data_origin="official",
            created_at=repo.now_iso(),
            active_dataset_id=None,
            generation=0,
        )
    )
    db.flush()
    dataset.family_id = family_id
    db.flush()
    return family_id


def _plan_bundle(student_id: str = "S01") -> RecommendationBundle:
    return RecommendationBundle.model_validate(
        {
            "run_id": RUN,
            "dataset_id": DATASET,
            "catalog_version": CATALOG,
            "student_id": student_id,
            "node_id": NODE,
            "analysis_version": "ban-1",
            "publication_status": "published",
            "recommendations": [
                {
                    "recommendation_id": "rec-1",
                    "indicator_id": "I01",
                    "finding_ids": ["F02"],
                    "goal": "稳定首次作答正确率",
                    "reason": "修订后能得满分，但首次作答仍有失分",
                    "student_intro": "今天先做两道小题，做完再检查一遍",
                    "group_id": "grp-1",
                    "review_status": "validated",
                    "generation_method": "rule",
                    "activities": [
                        {
                            "order": 1,
                            "activity_id": "act-1",
                            "group_id": "grp-1",
                            "material_id": "Q01",
                            "purpose": "巩固",
                            "observation_points": ["首次是否完整"],
                            "estimate_minutes": 4,
                        },
                        {
                            "order": 2,
                            "activity_id": "act-2",
                            "group_id": "grp-1",
                            "material_id": "Q02",
                            "purpose": "变式",
                            "observation_points": ["是否主动检查"],
                            "estimate_minutes": 4,
                        },
                        {
                            "order": 3,
                            "activity_id": "act-lesson",
                            "group_id": "grp-1",
                            "activity_kind": "micro_lesson",
                            "content_version": LESSON,
                            "content_ref": {
                                "kind": "personalized",
                                "content_id": "lesson-1",
                                "content_version": LESSON,
                            },
                            "purpose": "看一遍去括号讲解",
                            "observation_points": ["是否读完"],
                            "estimate_minutes": 3,
                        },
                    ],
                    "adjustment_conditions": [
                        {"observable_condition": "两道题都独立完成", "next_action": "进入迁移练习"}
                    ],
                }
            ],
            "validation_issues": [],
        }
    )


def _lesson_version(plan_version: str = PLAN) -> dict:
    return {
        "content_id": "lesson-1",
        "content_version": LESSON,
        "schema_version": "1.0",
        "creation_method": "rule",
        "body": {
            "title": "去括号时的符号处理",
            "goal": "看清括号前的符号",
            "estimated_minutes": 3,
            "kp_ids": ["K1"],
            "components": [
                {"type": "text", "id": "c1", "paragraphs": ["先看括号前的符号，再决定每一项的符号。"]}
            ],
        },
        "body_hash": "hash-lesson",
        "review_info": {"status": "validated", "method": "rule", "checks": []},
        "publication_status": "published",
        "origin_plan_version": plan_version,
    }


@pytest.fixture
def formal_plan(session, seeded) -> dict:
    family_id = _family(session, seeded["dataset_id"])
    key = repo.FormalKey(
        family_id=family_id, dataset_id=DATASET, kind="plan", node_key=NODE, target_key="S01"
    )
    version = repo.create_version(
        session,
        kind="plan",
        key=key,
        payload=_plan_bundle().model_dump(mode="json"),
        rule_version="c-rules-v1",
        input_digest="plan-input",
        version_id=PLAN,
        run_id=RUN,
    )
    repo.publish(session, key=key, version=version, path="system_auto", basis="automatic_review")
    lesson_key = repo.FormalKey(
        family_id=family_id, dataset_id=DATASET, kind="lesson", target_key="lesson-1"
    )
    lesson = repo.create_version(
        session,
        kind="lesson",
        key=lesson_key,
        payload=_lesson_version(),
        rule_version="lesson-rules-v1",
        version_id=LESSON,
        run_id=RUN,
    )
    repo.publish(
        session, key=lesson_key, version=lesson, path="system_auto", basis="automatic_review"
    )
    session.add(
        models.PlanRevision(
            revision_id="rev-1",
            proposal_id=None,
            proposal_revision=None,
            scope_key=scope_key_for(DATASET, "S01", NODE),
            base_plan_version=None,
            restore_version=None,
            result_version=PLAN,
            reason="首次发布",
            origin="system_auto",
            job_id=None,
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    return {"family_id": family_id, "plan_version": PLAN}


@pytest.fixture
def two_classes(session, seeded) -> list[str]:
    left = session.get(models.AnalysisResult, (RUN, "class", "C01", NODE))
    right = copy.deepcopy(left.payload_json)
    right["class_id"] = "C02"
    right["roster_count"] = 3
    right["metrics"][0]["metric_id"] = "M02"
    right["metrics"][0]["value"] = 0.5
    right["metrics"][0]["numerator"] = 1
    session.add(
        models.AnalysisResult(
            run_id=RUN, subject_type="class", subject_id="C02", node_id=NODE, payload_json=right
        )
    )
    session.flush()
    return ["C01", "C02"]


def _context(
    db,
    *,
    role: str = "teacher",
    student: str = "S01",
    allowed_students: tuple[str, ...] = ("S01", "S02"),
    classes: tuple[str, ...] | None = ("C01", "C02"),
) -> ServerContext:
    return ServerContext(
        run_id=RUN,
        dataset_id=DATASET,
        catalog_version=CATALOG,
        allowed_student_ids=frozenset(allowed_students),
        bundle=None,
        dataset=None,
        bound_student_id=student,
        bound_class_id=classes[0] if classes else None,
        bound_node_id=NODE,
        allowed_class_ids=frozenset(classes) if classes else None,
        extras={"db_session": db, "owner_ref": "session-test"},
    )


def _registry(db, task: str, *, role: str = "teacher", **kwargs):
    return build_b_registry(_context(db, role=role, **kwargs), role, get_task(task))


def _names(registry) -> set[str]:
    return {schema["function"]["name"] for schema in registry.schemas()}


def _call(registry, name: str, **arguments):
    return registry.call(ToolCall(call_id="call-1", name=name, arguments=arguments))


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #


def test_student_cannot_compare_classes_or_read_quality_report(session, seeded) -> None:
    student = _registry(session, "class_analysis", role="student")
    assert "compare_classes" not in _names(student)
    with pytest.raises(DomainError) as denied:
        _call(student, "compare_classes", class_ids=["C01", "C02"])
    assert denied.value.code == "TOOL_NOT_ALLOWED"

    quality = _registry(session, "data_quality", role="student")
    assert "get_quality_report" not in _names(quality)
    with pytest.raises(DomainError) as denied_quality:
        _call(quality, "get_quality_report", module="B")
    assert denied_quality.value.code == "TOOL_NOT_ALLOWED"

    teacher = _registry(session, "class_analysis", role="teacher")
    assert {"compare_classes", "resolve_evidence"} <= _names(teacher)


# --------------------------------------------------------------------------- #
# 进展
# --------------------------------------------------------------------------- #


def test_progress_without_baseline_says_not_comparable(session, seeded) -> None:
    registry = _registry(session, "student_progress", role="teacher")
    result = _call(registry, "get_student_progress", student_id="S01", node_id=NODE)
    data = result["data"]
    assert data["comparable"] is False
    assert data["comparisons"] == []
    assert any("基线" in item for item in data["limitations"])
    assert result["limitations"]


def test_progress_rejects_unlisted_student(session, seeded) -> None:
    registry = _registry(session, "student_progress", role="teacher")
    with pytest.raises(DomainError) as denied:
        _call(registry, "get_student_progress", student_id="S99", node_id=NODE)
    assert denied.value.code == "SCOPE_DENIED"


# --------------------------------------------------------------------------- #
# 方案理由 / 历史
# --------------------------------------------------------------------------- #


def test_plan_reason_reads_stored_reason_only(session, seeded, formal_plan) -> None:
    registry = _registry(session, "study_plan", role="teacher")
    result = _call(registry, "get_plan_reason", student_id="S01")
    data = result["data"]
    assert data["plan_version"] == PLAN
    assert data["groups"][0]["reason"] == "修订后能得满分，但首次作答仍有失分"
    assert any("未重新生成" in item for item in result["limitations"])

    student = _call(_registry(session, "study_plan", role="student"), "get_plan_reason", student_id="S01")
    group = student["data"]["groups"][0]
    assert "finding_ids" not in group
    assert "student_intro" in group


def test_plan_history_hides_drafts_from_students(session, seeded, formal_plan) -> None:
    session.add(
        models.PlanRevision(
            revision_id="rev-draft",
            proposal_id="prop-1",
            proposal_revision=1,
            scope_key=scope_key_for(DATASET, "S01", NODE),
            base_plan_version=PLAN,
            restore_version=None,
            result_version=None,
            reason="教师草稿",
            origin="teacher_direct",
            job_id=None,
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    teacher = _call(_registry(session, "study_plan", role="teacher"), "get_plan_history", student_id="S01")
    student = _call(_registry(session, "study_plan", role="student"), "get_plan_history", student_id="S01")
    assert teacher["data"]["total"] == 2
    assert student["data"]["total"] == 1
    assert all(item["plan_version"] for item in student["data"]["items"])


# --------------------------------------------------------------------------- #
# 材料检索
# --------------------------------------------------------------------------- #


def test_search_learning_materials_crops_student_answers(session, seeded) -> None:
    material = session.get(models.Material, (CATALOG, "Q01"))
    material.payload_json = {
        **material.payload_json,
        "reference_answer": "x=3",
        "reference_explanation": "去括号后移项",
    }
    session.flush()

    teacher = _call(
        _registry(session, "knowledge_lookup", role="teacher"),
        "search_learning_materials",
        purpose="practice",
    )
    assert teacher["data"]["total"] >= 1
    assert teacher["data"]["items"][0]["reference_answer"] == "x=3"

    student = _call(
        _registry(session, "knowledge_lookup", role="student"),
        "search_learning_materials",
        purpose="practice",
    )
    payload = str(student["data"])
    assert "reference_answer" not in payload
    assert "reference_explanation" not in payload
    assert all("reference_answer" not in item for item in student["data"]["items"])
    assert student["evidence_refs"]


# --------------------------------------------------------------------------- #
# 微课堂
# --------------------------------------------------------------------------- #


def test_lesson_view_reads_published_body(session, seeded, formal_plan) -> None:
    result = _call(
        _registry(session, "observation_review", role="student"),
        "get_lesson_view",
        activity_id="act-lesson",
    )
    lesson = result["data"]["lesson"]
    assert lesson["title"] == "去括号时的符号处理"
    assert lesson["plan_version"] == PLAN
    assert any("不重新生成" in item for item in result["limitations"])


def test_lesson_view_refuses_unpublished_version(session, seeded, formal_plan) -> None:
    row = session.get(models.Version, LESSON)
    row.payload_json = {**row.payload_json, "publication_status": "unpublished"}
    head = session.get(models.FormalHead, (formal_plan["family_id"], DATASET, "lesson", "", "", "lesson-1"))
    session.delete(head)
    session.flush()
    with pytest.raises(DomainError) as blocked:
        _call(
            _registry(session, "observation_review", role="teacher"),
            "get_lesson_view",
            activity_id="act-lesson",
        )
    assert blocked.value.code == "RESULT_NOT_READY"


# --------------------------------------------------------------------------- #
# 班级比较与质量报告
# --------------------------------------------------------------------------- #


def test_compare_classes_uses_b_alignment_and_counts(session, seeded, two_classes) -> None:
    result = _call(
        _registry(session, "class_analysis", role="teacher"),
        "compare_classes",
        class_ids=["C01", "C02"],
        node_id=NODE,
    )
    data = result["data"]
    assert data["comparable"] is True
    assert data["items"][0]["indicator_id"] == "I01"
    assert data["items"][0]["delta"] == pytest.approx(-0.5)
    assert any("不提供总分差" in item for item in data["limitations"])
    assert result["evidence_refs"]


def test_compare_classes_rejects_unauthorized_class(session, seeded, two_classes) -> None:
    with pytest.raises(DomainError) as denied:
        _call(
            _registry(session, "class_analysis", role="teacher"),
            "compare_classes",
            class_ids=["C01", "C03"],
        )
    assert denied.value.code == "SCOPE_DENIED"


def test_quality_report_b_is_deterministic(session, seeded) -> None:
    result = _call(
        _registry(session, "data_quality", role="teacher"),
        "get_quality_report",
        module="B",
        node_id=NODE,
    )
    report = result["data"]["report"]
    assert report["counts"]["selected_events"] >= 1
    assert report["coverage"]
    assert result["limitations"]

    with pytest.raises(DomainError) as missing:
        _call(_registry(session, "data_quality", role="teacher"), "get_quality_report", module="A")
    assert missing.value.code == "RESULT_NOT_READY"


# --------------------------------------------------------------------------- #
# 证据解析
# --------------------------------------------------------------------------- #


def test_resolve_evidence_reauthorizes_by_role(session, seeded) -> None:
    teacher = _call(
        _registry(session, "evidence_lookup", role="teacher"),
        "resolve_evidence",
        refs=[{"kind": "record", "id": "R02", "run_id": RUN}],
    )
    assert teacher["data"]["items"][0]["detail_type"] == "record"

    student_registry = _registry(session, "evidence_lookup", role="student")
    with pytest.raises(DomainError) as forbidden:
        _call(student_registry, "resolve_evidence", refs=[{"kind": "record", "id": "R05", "run_id": RUN}])
    assert forbidden.value.code == "FORBIDDEN"

    with pytest.raises(DomainError) as mismatch:
        _call(
            _registry(session, "evidence_lookup", role="teacher"),
            "resolve_evidence",
            refs=[{"kind": "record", "id": "R02", "run_id": RUN, "dataset_id": "other-ds"}],
        )
    assert mismatch.value.code == "SCOPE_DENIED"


# --------------------------------------------------------------------------- #
# 修订候选
# --------------------------------------------------------------------------- #


def _intent(group_id: str = "grp-1") -> dict:
    return {
        "goal": "把题量减到一道",
        "reason": "学生一次能专注的时间有限",
        "requested_changes": [
            {"kind": "reduce_questions", "group_id": group_id, "max_questions": 1}
        ],
    }


def test_prepare_plan_revision_writes_candidate_only(session, seeded, formal_plan) -> None:
    plans_before = set(
        session.scalars(select(models.Version.version_id).where(models.Version.kind == "plan")).all()
    )
    head_before = session.get(
        models.FormalHead, (formal_plan["family_id"], DATASET, "plan", "", NODE, "S01")
    ).version_id

    result = _call(
        _registry(session, "study_plan", role="teacher"),
        "prepare_plan_revision",
        student_id="S01",
        base_plan_version=PLAN,
        intent=_intent(),
    )
    data = result["data"]
    assert data["side_effects"] == "candidate_only"
    assert data["formal_plan_version"] is None
    assert data["proposal"]["base_plan_version"] == PLAN
    assert data["proposal"]["status"] == "ready"

    plans_after = set(
        session.scalars(select(models.Version.version_id).where(models.Version.kind == "plan")).all()
    )
    head_after = session.get(
        models.FormalHead, (formal_plan["family_id"], DATASET, "plan", "", NODE, "S01")
    ).version_id
    assert plans_after == plans_before
    assert head_after == head_before
    proposal = session.get(models.Proposal, (data["proposal"]["proposal_id"], 1))
    assert proposal is not None and proposal.state == "ready"


def test_prepare_plan_revision_validates_student_and_baseline(session, seeded, formal_plan) -> None:
    registry = _registry(session, "study_plan", role="teacher")
    with pytest.raises(DomainError) as student_mismatch:
        _call(
            registry,
            "prepare_plan_revision",
            student_id="S02",
            base_plan_version=PLAN,
            intent=_intent(),
        )
    assert student_mismatch.value.code == "CONTEXT_MISMATCH"

    with pytest.raises(DomainError) as stale:
        _call(
            registry,
            "prepare_plan_revision",
            student_id="S01",
            base_plan_version="plan-old",
            intent=_intent(),
        )
    assert stale.value.code == "DEPENDENCY_STALE"

    with pytest.raises(DomainError) as unsupported:
        _call(
            registry,
            "prepare_plan_revision",
            student_id="S01",
            base_plan_version=PLAN,
            intent={
                "goal": "新增讲解",
                "reason": "学生需要微课堂",
                "requested_changes": [{"kind": "add_lesson", "kp_ids": ["K1"], "teaching_focus": "符号"}],
            },
        )
    assert unsupported.value.code == "TASK_NOT_AVAILABLE"

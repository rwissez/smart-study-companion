"""05 §7 / 04 §9.3：Agent 方案修改闭环（候选 → 提交 → 状态 → 教师确认）。

这里验证的是最小可交付闭环：提交只创建待确认记录，不发布；状态只读；
学生不能使用修订工具；越权目标被拒绝；重复提交返回同一确认。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.agent import proposals as proposal_rules
from study_companion.agent.tasks import get_task
from study_companion.agent.tools import build_b_registry
from study_companion.agent.types import ToolCall
from study_companion.contracts import DomainError, RecommendationBundle
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.tools.b_analysis import ServerContext

DATASET = "synthetic-ds"
CATALOG = "synthetic-v1"
RUN = "synthetic-run"
NODE = "N01"
PLAN = "plan-v1"
OWNER = "session-teacher"


# --------------------------------------------------------------------------- #
# 夹具：合成数据上补一份带任务组的正式方案
# --------------------------------------------------------------------------- #


def _family(db) -> str:
    dataset = db.get(models.Dataset, DATASET)
    if dataset.family_id:
        return dataset.family_id
    family_id = "fam-revision"
    db.add(
        models.DatasetFamily(
            family_id=family_id,
            label="修订闭环合成系列",
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


def _plan_bundle() -> RecommendationBundle:
    return RecommendationBundle.model_validate(
        {
            "run_id": RUN,
            "dataset_id": DATASET,
            "catalog_version": CATALOG,
            "student_id": "S01",
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
                    ],
                    "adjustment_conditions": [
                        {
                            "observable_condition": "两道题都独立完成",
                            "next_action": "进入迁移练习",
                        }
                    ],
                }
            ],
            "task_groups": [
                {
                    "group_id": "grp-1",
                    "goal": "稳定首次作答正确率",
                    "activity_ids": ["act-1", "act-2"],
                    "order": 1,
                }
            ],
            "validation_issues": [],
        }
    )


@pytest.fixture
def formal_plan(session, seeded) -> dict:
    family_id = _family(session)
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
    repo.publish(
        session, key=key, version=version, path="system_auto", basis="automatic_review"
    )
    session.flush()
    return {"family_id": family_id, "plan_version": PLAN}


# --------------------------------------------------------------------------- #
# 上下文与调用辅助
# --------------------------------------------------------------------------- #


def _context(
    db,
    *,
    role: str = "teacher",
    student: str | None = "S01",
    allowed: tuple[str, ...] = ("S01", "S02"),
    dataset: str = DATASET,
    extras: dict | None = None,
) -> ServerContext:
    merged = {"db_session": db, "owner_ref": OWNER, "conversation_id": "conv-1"}
    if extras:
        merged.update(extras)
    return ServerContext(
        run_id=RUN,
        dataset_id=dataset,
        catalog_version=CATALOG,
        allowed_student_ids=frozenset(allowed),
        bundle=None,
        dataset=None,
        bound_student_id=student,
        bound_class_id=None,
        bound_node_id=NODE,
        allowed_class_ids=None,
        extras=merged,
    )


def _teacher_registry(db, **kwargs):
    return build_b_registry(_context(db, **kwargs), "teacher", get_task("study_plan"))


def _student_registry(db):
    ctx = _context(db, role="student", student="S01", allowed=("S01",))
    return build_b_registry(ctx, "student", get_task("study_plan"))


def _names(registry) -> set[str]:
    return {schema["function"]["name"] for schema in registry.schemas()}


def _call(registry, name: str, **arguments):
    return registry.call(ToolCall(call_id="call-1", name=name, arguments=arguments))


def _intent() -> dict:
    return {
        "goal": "把题量减到一道",
        "reason": "学生一次能专注的时间有限",
        "requested_changes": [
            {"kind": "reduce_questions", "group_id": "grp-1", "max_questions": 1}
        ],
    }


def _prepare(registry) -> str:
    result = _call(
        registry,
        "prepare_plan_revision",
        student_id="S01",
        base_plan_version=PLAN,
        intent=_intent(),
    )
    return result["data"]["proposal"]["proposal_id"]


def _head(session, family_id: str) -> str | None:
    row = session.get(models.FormalHead, (family_id, DATASET, "plan", "", NODE, "S01"))
    return row.version_id if row else None


# --------------------------------------------------------------------------- #
# 闭环：候选 → 提交 → 状态 → 教师确认
# --------------------------------------------------------------------------- #


def test_candidate_submit_status_then_teacher_confirmation(session, seeded, formal_plan) -> None:
    registry = _teacher_registry(session)
    proposal_id = _prepare(registry)
    assert session.get(models.Proposal, (proposal_id, 1)).state == "ready"

    draft = _call(registry, "get_revision_status", proposal_id=proposal_id)["data"]
    assert draft["status"] == "draft"
    assert draft["can_submit"] is True
    assert draft["can_publish"] is False
    assert draft["plan_version"] is None

    head_before = _head(session, formal_plan["family_id"])
    submitted = _call(registry, "submit_plan_revision", proposal_id=proposal_id)["data"]
    assert submitted["status"] == "pending"
    assert submitted["publication_status"] == "unpublished"
    assert submitted["requires_teacher_confirmation"] is True
    assert submitted["idempotent"] is False
    assert submitted["side_effects"] == "confirmation_created"
    confirmation_id = submitted["confirmation_id"]
    assert confirmation_id

    # 提交不发布：正式指针、方案版本和操作账本都不动。
    assert _head(session, formal_plan["family_id"]) == head_before
    assert session.scalars(select(models.AgentOperation)).all() == []
    assert (
        session.scalars(
            select(models.PlanRevision).where(models.PlanRevision.proposal_id == proposal_id)
        ).all()
        == []
    )
    assert session.get(models.Proposal, (proposal_id, 1)).state == "ready"

    pending = _call(registry, "get_revision_status", proposal_id=proposal_id)["data"]
    assert pending["status"] == "pending"
    assert pending["confirmation_id"] == confirmation_id
    assert pending["can_submit"] is False
    assert pending["next_step"] == "await_teacher_confirmation"

    by_confirmation = _call(
        registry, "get_revision_status", confirmation_id=confirmation_id
    )["data"]
    assert by_confirmation["proposal_id"] == proposal_id
    assert by_confirmation["status"] == "pending"

    # 教师确认走既有 proposal/confirm 服务，发布仍是 04 的 agent_revision。
    confirmation = session.get(models.Confirmation, confirmation_id)
    dataset = session.get(models.Dataset, DATASET)
    decided = proposal_rules.decide_confirmation(
        session,
        confirmation=confirmation,
        allow=True,
        actor=OWNER,
        dataset=dataset,
        node_id=NODE,
    )
    session.flush()
    assert decided["decision"] == "accepted"
    operation = decided["operations"][0]
    assert operation["publication_status"] == "published"
    new_version = operation["new_plan_version"]
    assert new_version and new_version != head_before
    assert _head(session, formal_plan["family_id"]) == new_version

    confirmed = _call(registry, "get_revision_status", proposal_id=proposal_id)["data"]
    assert confirmed["status"] == "confirmed"
    assert confirmed["publication_status"] == "published"
    assert confirmed["plan_version"] == new_version
    assert confirmed["revision_id"]
    assert confirmed["next_step"] == "none"

    by_revision = _call(
        registry, "get_revision_status", revision_id=confirmed["revision_id"]
    )["data"]
    assert by_revision["status"] == "confirmed"
    assert by_revision["plan_version"] == new_version


def test_submit_is_idempotent_and_keeps_one_confirmation(session, seeded, formal_plan) -> None:
    registry = _teacher_registry(session)
    proposal_id = _prepare(registry)
    head_before = _head(session, formal_plan["family_id"])

    first = _call(registry, "submit_plan_revision", proposal_id=proposal_id)["data"]
    second = _call(registry, "submit_plan_revision", proposal_id=proposal_id)["data"]

    assert first["confirmation_id"] == second["confirmation_id"]
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["side_effects"] == "none"
    assert second["status"] == "pending"

    rows = [
        row
        for row in session.scalars(
            select(models.Confirmation).where(models.Confirmation.kind == "plan_revision")
        ).all()
        if any(
            (target or {}).get("proposal_id") == proposal_id
            for target in (row.targets_json or [])
        )
    ]
    assert len(rows) == 1
    assert _head(session, formal_plan["family_id"]) == head_before


def test_unconfirmed_candidate_never_publishes(session, seeded, formal_plan) -> None:
    registry = _teacher_registry(session)
    proposal_id = _prepare(registry)
    head_before = _head(session, formal_plan["family_id"])
    versions_before = set(
        session.scalars(select(models.Version.version_id).where(models.Version.kind == "plan")).all()
    )

    _call(registry, "submit_plan_revision", proposal_id=proposal_id)

    assert _head(session, formal_plan["family_id"]) == head_before
    versions_after = set(
        session.scalars(select(models.Version.version_id).where(models.Version.kind == "plan")).all()
    )
    assert versions_after == versions_before
    assert session.get(models.Proposal, (proposal_id, 1)).state == "ready"
    status = _call(registry, "get_revision_status", proposal_id=proposal_id)["data"]
    assert status["status"] == "pending"
    assert status["publication_status"] == "unpublished"
    assert status["plan_version"] is None
    assert status["can_publish"] is False


# --------------------------------------------------------------------------- #
# 错误与权限
# --------------------------------------------------------------------------- #


def test_student_cannot_use_revision_tools(session, seeded, formal_plan) -> None:
    registry = _student_registry(session)
    assert "prepare_plan_revision" not in _names(registry)
    assert "submit_plan_revision" not in _names(registry)
    assert "get_revision_status" not in _names(registry)

    with pytest.raises(DomainError) as submit_denied:
        _call(registry, "submit_plan_revision", proposal_id="prop-unknown")
    assert submit_denied.value.code == "TOOL_NOT_ALLOWED"

    with pytest.raises(DomainError) as status_denied:
        _call(registry, "get_revision_status", proposal_id="prop-unknown")
    assert status_denied.value.code == "TOOL_NOT_ALLOWED"


def test_submit_rejects_out_of_scope_targets(session, seeded, formal_plan) -> None:
    proposal_id = _prepare(_teacher_registry(session))

    other_student = _teacher_registry(
        session, student=None, allowed=("S02",)
    )
    with pytest.raises(DomainError) as denied_student:
        _call(other_student, "submit_plan_revision", proposal_id=proposal_id)
    assert denied_student.value.code == "SCOPE_DENIED"

    other_dataset = _teacher_registry(session, dataset="other-ds")
    with pytest.raises(DomainError) as denied_dataset:
        _call(other_dataset, "submit_plan_revision", proposal_id=proposal_id)
    assert denied_dataset.value.code == "FORBIDDEN"

    bound_elsewhere = _teacher_registry(session, student="S02", allowed=("S01", "S02"))
    with pytest.raises(DomainError) as denied_bound:
        _call(bound_elsewhere, "submit_plan_revision", proposal_id=proposal_id)
    assert denied_bound.value.code == "CONTEXT_MISMATCH"

    with pytest.raises(DomainError) as denied_status:
        _call(other_student, "get_revision_status", proposal_id=proposal_id)
    assert denied_status.value.code == "SCOPE_DENIED"


def test_submit_and_status_reject_missing_or_invalid_candidate(session, seeded, formal_plan) -> None:
    registry = _teacher_registry(session)

    with pytest.raises(DomainError) as missing:
        _call(registry, "submit_plan_revision", proposal_id="prop-unknown")
    assert missing.value.code == "NOT_FOUND"
    assert "候选" in missing.value.message

    with pytest.raises(DomainError) as no_selector:
        _call(registry, "get_revision_status")
    assert no_selector.value.code == "INVALID_ARGUMENT"

    with pytest.raises(DomainError) as unknown:
        _call(registry, "get_revision_status", proposal_id="prop-unknown")
    assert unknown.value.code == "NOT_FOUND"

    proposal_id = _prepare(registry)
    proposal = session.get(models.Proposal, (proposal_id, 1))
    proposal.state = "rejected"
    session.flush()
    with pytest.raises(DomainError) as rejected:
        _call(registry, "submit_plan_revision", proposal_id=proposal_id)
    assert rejected.value.code == "CONFLICT"
    assert "拒绝" in rejected.value.message

    proposal.state = "ready"
    proposal.expires_at = "2000-01-01T00:00:00+00:00"
    session.flush()
    with pytest.raises(DomainError) as expired:
        _call(registry, "submit_plan_revision", proposal_id=proposal_id)
    assert expired.value.code == "REVISION_CONFLICT"
    assert "过期" in expired.value.message


def test_api_revision_endpoints_reject_student(client: TestClient, seeded: dict) -> None:
    token = client.post(
        "/api/v1/demo-sessions",
        json={
            "role": "student",
            "dataset_id": seeded["dataset_id"],
            "student_id": "S01",
        },
    ).json()["data"]["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    submitted = client.post(
        f"/api/v1/datasets/{seeded['dataset_id']}/plan-revisions/prop-1/submit",
        headers=headers,
        json={},
    )
    assert submitted.status_code == 403

    status = client.get(
        f"/api/v1/datasets/{seeded['dataset_id']}/revision-status",
        headers=headers,
        params={"proposal_id": "prop-1"},
    )
    assert status.status_code == 403

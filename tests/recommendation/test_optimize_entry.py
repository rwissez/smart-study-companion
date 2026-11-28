"""教师意图优化入口：成功建议、非法 id、已完成活动、模型失败与预览衔接（04 §5.2）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.api.routes.plan_edit import get_optimize_model_service
from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    Recommendation,
    RecommendationBundle,
    RevisionIntent,
    TaskGroup,
)
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.db.session import session_scope
from study_companion.recommendation import optimize_service
from study_companion.recommendation.optimize_model import (
    OptimizeCandidate,
    OptimizeChange,
    OptimizeChangeOut,
    OptimizeOut,
    OptimizeOutcome,
)

DATASET_ID = "ds-opt"
FAMILY_ID = "fam-opt"
RUN_ID = "run-opt"
NODE_ID = "N01"
CATALOG_VERSION = "cat-opt"
PLAN_VERSION = "plan-opt-1"
STUDENT_ID = "S01"
STUDENT_UID = "uid-opt-1"
INTENT = "把重复练习换成一道独立检查题，减轻书写负担"


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    """替身模型：只记录请求，返回固定的结构化输出。"""

    def __init__(self, value: OptimizeOut | None, *, code: str = "MODEL_TIMEOUT") -> None:
        self.value = value
        self.code = code
        self.payloads: list[dict] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.payloads.append(request.input_payload)
        if self.value is None:
            return Result(False, None, {"code": self.code})
        return Result(True, self.value)


def _material(material_id: str) -> dict:
    return {
        "material_id": material_id,
        "material_type": "question",
        "topic_ids": ["T1"],
        "indicator_ids": ["M1"],
        "content": f"化简含括号的式子（{material_id}）",
        "source_locator": {"source_file": "synthetic.csv", "verification_status": "verified"},
        "question_type": "solution",
        "source_label": "合成材料",
    }


def _activity(order: int) -> Activity:
    return Activity(
        order=order,
        material_id=f"MAT-{order}",
        activity_id=f"act-{order}",
        purpose="练「去括号」的符号分配",
        hint_levels=["回忆分配律", "把括号前看作乘 -1", "逐项核对符号"],
        title=f"化简 -(2x-{order})",
        estimate_minutes=4,
        state="not_started",
        knowledge_ids=["M1"],
        group_id="grp-1",
        exposure_mode="practice",
    )


def _bundle() -> RecommendationBundle:
    recommendation = Recommendation(
        recommendation_id="rec-1",
        indicator_id="M1",
        finding_ids=[],
        goal="围绕「去括号时的符号处理」安排本轮学习",
        reason="存在具体错误证据",
        student_intro="我们先从去括号开始。",
        activities=[_activity(1), _activity(2), _activity(3)],
        adjustment_conditions=[
            AdjustmentCondition(observable_condition="独立题全对", next_action="下一轮升级为变式")
        ],
        strategy_types=["error_correction"],
        kp_ids=["M1"],
        group_id="grp-1",
    )
    return RecommendationBundle(
        run_id=RUN_ID,
        dataset_id=DATASET_ID,
        catalog_version=CATALOG_VERSION,
        student_id=STUDENT_ID,
        node_id=NODE_ID,
        recommendations=[recommendation],
        validation_issues=[],
        plan_version=PLAN_VERSION,
        analysis_version="ban-1",
        task_groups=[
            TaskGroup(
                group_id="grp-1",
                goal="围绕「去括号时的符号处理」安排本轮学习",
                strategy_types=["error_correction"],
                activity_ids=["act-1", "act-2", "act-3"],
                order=1,
                primary_strategy="error_correction",
                goal_target_key="M1",
                required_activity_ids=["act-1", "act-2", "act-3"],
            )
        ],
        publication_status="published",
    )


def _build_world() -> None:
    now = repo.now_iso()
    with session_scope() as db:
        db.add(
            models.DatasetFamily(
                family_id=FAMILY_ID,
                label="合成优化系列",
                data_origin="synthetic",
                created_at=now,
                generation=1,
            )
        )
        db.flush()
        db.add(
            models.Dataset(
                id=DATASET_ID,
                created_at=now,
                parent_id=None,
                status="ready",
                family_id=FAMILY_ID,
                manifest_json={"synthetic": True},
                state="ready",
            )
        )
        db.flush()
        db.add(
            models.Catalog(
                version=CATALOG_VERSION,
                dataset_id=DATASET_ID,
                content_hash="0" * 64,
                created_at=now,
                payload_json={"topics": [], "links": []},
            )
        )
        for order in (1, 2, 3):
            db.add(
                models.Material(
                    catalog_version=CATALOG_VERSION,
                    material_id=f"MAT-{order}",
                    payload_json=_material(f"MAT-{order}"),
                )
            )
        # MAT-4 只在候选池里，不在当前方案。
        db.add(
            models.Material(
                catalog_version=CATALOG_VERSION,
                material_id="MAT-4",
                payload_json=_material("MAT-4"),
            )
        )
        db.add(
            models.Node(
                dataset_id=DATASET_ID,
                node_id=NODE_ID,
                payload_json={
                    "node_id": NODE_ID,
                    "available_question_ids": ["MAT-1", "MAT-2", "MAT-3", "MAT-4"],
                },
                scope_kind="assessment",
                scope_json={},
            )
        )
        db.add(
            models.StudentIdentity(
                student_uid=STUDENT_UID,
                family_id=FAMILY_ID,
                external_id=STUDENT_ID,
                created_at=now,
            )
        )
        db.add(
            models.Student(
                dataset_id=DATASET_ID,
                student_id=STUDENT_ID,
                class_id="C1",
                student_uid=STUDENT_UID,
            )
        )
        db.flush()
        db.add(
            models.AnalysisRun(
                id=RUN_ID,
                dataset_id=DATASET_ID,
                catalog_version=CATALOG_VERSION,
                rule_version="b-rules-1",
                config_json={},
                status="succeeded",
                created_at=now,
                event_cutoff=3,
            )
        )
        db.flush()

        key = repo.FormalKey(
            family_id=FAMILY_ID,
            dataset_id=DATASET_ID,
            kind="plan",
            node_key=NODE_ID,
            target_key=STUDENT_ID,
        )
        version = repo.create_version(
            db,
            kind="plan",
            key=key,
            payload=_bundle().model_dump(mode="json"),
            version_id=PLAN_VERSION,
            origin_kind="teacher",
        )
        repo.publish(db, key=key, version=version, path="system_auto", basis="automatic_review")
        for order in (1, 2, 3):
            db.add(
                models.ActivityStateRow(
                    activity_id=f"act-{order}",
                    origin_plan_version=PLAN_VERSION,
                    student_uid=STUDENT_UID,
                    state="not_started",
                    generation=1,
                    updated_at=now,
                )
            )


@pytest.fixture
def world(client: TestClient, app_env: Path) -> dict:
    _build_world()
    response = client.post(
        "/api/v1/demo-sessions", json={"role": "teacher", "dataset_id": DATASET_ID}
    )
    assert response.status_code == 201, response.text
    token = response.json()["data"]["session_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "url": f"/api/v1/runs/{RUN_ID}/students/{STUDENT_ID}/recommendation-revisions/optimize",
        "preview_url": (
            f"/api/v1/runs/{RUN_ID}/students/{STUDENT_ID}/recommendation-revisions/preview"
        ),
    }


@pytest.fixture
def fake_model(client: TestClient):
    def _install(value: OptimizeOut | None, *, code: str = "MODEL_TIMEOUT") -> FakeService:
        service = FakeService(value, code=code)
        client.app.dependency_overrides[get_optimize_model_service] = lambda: service
        return service

    yield _install
    client.app.dependency_overrides.pop(get_optimize_model_service, None)


def _new_ids() -> dict[str, int]:
    with session_scope() as db:
        return {
            "versions": len(db.scalars(select(models.Version)).all()),
            "proposals": len(db.scalars(select(models.Proposal)).all()),
            "revisions": len(db.scalars(select(models.PlanRevision)).all()),
        }


def test_proposal_is_read_only_and_previewable(
    client: TestClient, world: dict, fake_model
) -> None:
    service = fake_model(
        OptimizeOut(
            kind="proposal",
            reason="去掉重复练习，保留独立检查",
            changes=[
                OptimizeChangeOut(
                    op="remove",
                    group_key="G1",
                    activity_key="A2",
                    reason="与第一题结构重复，删掉后负担更合理",
                )
            ],
        )
    )
    before = _new_ids()

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "proposal"
    assert data["read_only"] is True
    assert data["model_used"] is True
    assert data["base_plan_version"] == PLAN_VERSION
    assert data["candidate_count"] == 1
    assert len(data["changes"]) == 1
    change = data["changes"][0]
    assert change["op"] == "remove"
    assert change["group_key"] == "G1"
    assert change["activity_key"] == "A2"
    assert change["activity_title"] == "化简 -(2x-2)"
    assert change["activity_source"] == "current_plan"
    assert change["applicable"] is True
    assert data["blocked_reasons"] == []
    assert data["preview_note"]

    activities = data["revision_changes"]["activities"]
    assert [item["activity_id"] for item in activities] == ["act-1", "act-3"]
    assert [item["order"] for item in activities] == [1, 2]

    # 模型只看到 A1..A3 和候选池里的 MAT-4，教师意图原样进入 payload。
    payload = service.payloads[0]
    assert [item["activity_key"] for item in payload["activities"]] == ["A1", "A2", "A3"]
    assert [item["activity_key"] for item in payload["candidates"]] == ["MAT-4"]
    assert payload["intent"]["goal"] == INTENT

    # 建议不写库：不新增方案版本、候选提案或修订记录。
    assert _new_ids() == before

    # 转换出的 RevisionChanges 可以直接进入现有 preview 流程。
    preview = client.post(
        world["preview_url"],
        headers=world["headers"],
        json={
            "node_id": NODE_ID,
            "base_plan_version": PLAN_VERSION,
            "reason": INTENT,
            "changes": data["revision_changes"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["preview_id"]
    # 仍然只是预览：正式方案版本没有被切换。
    with session_scope() as db:
        head = repo.read_head(
            db,
            repo.FormalKey(
                family_id=FAMILY_ID,
                dataset_id=DATASET_ID,
                kind="plan",
                node_key=NODE_ID,
                target_key=STUDENT_ID,
            ),
        )
        assert head is not None and head.version_id == PLAN_VERSION


def test_add_activity_is_read_only_without_revision_changes(
    client: TestClient, world: dict, fake_model
) -> None:
    fake_model(
        OptimizeOut(
            kind="proposal",
            reason="加入一道独立检查题",
            changes=[
                OptimizeChangeOut(
                    op="add",
                    group_key="G1",
                    activity_key="MAT-4",
                    reason="用同目标的独立检查替代重复练习",
                )
            ],
        )
    )

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "proposal"
    change = data["changes"][0]
    assert change["activity_source"] == "catalog"
    assert change["applicable"] is False
    assert "三级提示" in change["apply_blocker"]
    assert data["revision_changes"] is None
    assert data["blocked_reasons"] and "三级提示" in data["blocked_reasons"][0]
    # 只读建议仍保留，教师可以照着手动改。
    assert change["activity_title"].startswith("化简含括号的式子（MAT-4）")


def test_fabricated_activity_id_rejects_whole_proposal(
    client: TestClient, world: dict, fake_model
) -> None:
    fake_model(
        OptimizeOut(
            kind="proposal",
            reason="编造活动",
            changes=[
                OptimizeChangeOut(
                    op="add", group_key="G1", activity_key="Z9", reason="不在候选池"
                ),
                OptimizeChangeOut(
                    op="remove", group_key="G1", activity_key="A2", reason="合法但一起被拒"
                ),
            ],
        )
    )

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "rejected"
    assert data["changes"] == []
    assert data["revision_changes"] is None
    assert [issue["code"] for issue in data["issues"]] == ["UNAUTHORIZED_ACTIVITY_ID"]
    assert any("整份拒绝" in item for item in data["limitations"])


def test_completed_activity_is_protected(client: TestClient, world: dict, fake_model) -> None:
    with session_scope() as db:
        db.get(models.ActivityStateRow, "act-1").state = "completed"
    service = fake_model(
        OptimizeOut(
            kind="proposal",
            reason="删掉第一题",
            changes=[
                OptimizeChangeOut(
                    op="remove", group_key="G1", activity_key="A1", reason="第一题已经做过"
                )
            ],
        )
    )

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "rejected"
    assert data["changes"] == []
    assert data["issues"][0]["code"] == "COMPLETED_ACTIVITY_PROTECTED"
    assert "已完成" in data["issues"][0]["message"]
    payload = service.payloads[0]
    assert set(payload["frozen_activity_keys"]) == {"A1", "act-1"}
    states = {item["activity_key"]: item["state"] for item in payload["activities"]}
    assert states["A1"] == "completed"


def test_model_failure_degrades_to_unavailable(
    client: TestClient, world: dict, fake_model
) -> None:
    fake_model(None, code="MODEL_TIMEOUT")

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "unavailable"
    assert data["model_used"] is False
    assert data["changes"] == []
    assert data["revision_changes"] is None
    assert data["issues"] == []
    assert any("MODEL_TIMEOUT" in item for item in data["limitations"])


def test_unconfigured_model_reports_unavailable(client: TestClient, world: dict) -> None:
    """没有替身时走真实 ModelService：未配置就是 unavailable，不硬改方案。"""

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": PLAN_VERSION, "intent": INTENT},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "unavailable"
    assert data["model_used"] is False
    assert data["changes"] == []
    assert any("MODEL_NOT_CONFIGURED" in item for item in data["limitations"])


def test_stale_base_plan_version_is_rejected(
    client: TestClient, world: dict, fake_model
) -> None:
    fake_model(
        OptimizeOut(
            kind="proposal",
            reason="移除重复题",
            changes=[
                OptimizeChangeOut(
                    op="remove", group_key="G1", activity_key="A2", reason="重复"
                )
            ],
        )
    )

    response = client.post(
        world["url"],
        headers=world["headers"],
        json={"node_id": NODE_ID, "base_plan_version": "plan-old", "intent": INTENT},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"


# --------------------------------------------------------------------------- #
# 入口层单独校验：来源过滤、跨任务组的兼容边界
# --------------------------------------------------------------------------- #


def test_illegal_candidate_source_is_rejected_at_entry() -> None:
    service = FakeService(
        OptimizeOut(
            kind="proposal",
            reason="加一道外部题",
            changes=[
                OptimizeChangeOut(
                    op="add", group_key="G1", activity_key="X1", reason="外部来源"
                )
            ],
        )
    )

    outcome = optimize_service.optimize_teacher_plan(
        service=service,
        bundle=_bundle(),
        intent=RevisionIntent(goal=INTENT, reason=INTENT),
        candidates=[
            OptimizeCandidate(
                activity_key="X1", purpose="未核验的外部题", source="external_unverified"
            )
        ],
    )

    assert outcome.kind == "rejected"
    assert outcome.changes == []
    assert outcome.issues[0].code == "UNAUTHORIZED_ACTIVITY_SOURCE"
    assert service.payloads[0]["candidates"] == []


def test_reorder_in_first_group_becomes_revision_changes() -> None:
    outcome = OptimizeOutcome(
        kind="proposal",
        reason="先做独立检查",
        model_used=True,
        changes=[
            OptimizeChange(
                op="reorder",
                group_key="G1",
                activity_key="A3",
                from_activity_key=None,
                position=1,
                reason="独立检查放到最前",
            )
        ],
    )

    view = optimize_service.build_optimize_view(
        outcome, _bundle(), [], base_plan_version=PLAN_VERSION
    )

    assert view["changes"][0]["applicable"] is True
    activities = view["revision_changes"]["activities"]
    assert [item["activity_id"] for item in activities] == ["act-3", "act-1", "act-2"]
    assert [item["order"] for item in activities] == [1, 2, 3]


def test_change_in_second_group_is_read_only_with_reason() -> None:
    bundle = _bundle()
    second = Recommendation(
        recommendation_id="rec-2",
        indicator_id="M2",
        finding_ids=[],
        goal="巩固变式",
        reason="第二个目标",
        activities=[_activity(4)],
        adjustment_conditions=[],
        strategy_types=["consolidation"],
        group_id="grp-2",
    )
    bundle = bundle.model_copy(
        update={"recommendations": [bundle.recommendations[0], second]}
    )
    outcome = OptimizeOutcome(
        kind="proposal",
        reason="第二组减一题",
        model_used=True,
        changes=[
            OptimizeChange(
                op="remove",
                group_key="G2",
                activity_key="A4",
                from_activity_key=None,
                position=None,
                reason="第二组只需要一题",
            )
        ],
    )

    view = optimize_service.build_optimize_view(
        outcome, bundle, [], base_plan_version=PLAN_VERSION
    )

    assert view["changes"][0]["applicable"] is False
    assert "第一个任务组" in view["changes"][0]["apply_blocker"]
    assert view["blocked_reasons"]
    assert view["revision_changes"] is None

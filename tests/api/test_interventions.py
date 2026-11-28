"""教师介入：详情读取、结案、并发水位、越权与审计（01 §9.3、04 §7、06 §4.4）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.api.routes.interventions import ensure_intervention
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.db.session import get_sessionmaker, session_scope

DATASET_ID = "ds-i"
FAMILY_ID = "fam-i"
RUN_ID = "run-1"
NODE_ID = "N01"


def _build_world() -> dict:
    now = repo.now_iso()
    with session_scope() as db:
        db.add(
            models.DatasetFamily(
                family_id=FAMILY_ID,
                label="合成介入系列",
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
                version="cat-i",
                dataset_id=DATASET_ID,
                content_hash="0" * 64,
                created_at=now,
                payload_json={"topics": [], "links": []},
            )
        )
        db.add(
            models.Node(
                dataset_id=DATASET_ID,
                node_id=NODE_ID,
                payload_json={"node_id": NODE_ID},
                scope_kind="assessment",
                scope_json={},
            )
        )
        for student_uid, external_id, class_id in (
            ("uid-1", "S01", "C1"),
            ("uid-2", "S02", "C2"),
        ):
            db.add(
                models.StudentIdentity(
                    student_uid=student_uid,
                    family_id=FAMILY_ID,
                    external_id=external_id,
                    created_at=now,
                )
            )
            db.add(
                models.Student(
                    dataset_id=DATASET_ID,
                    student_id=external_id,
                    class_id=class_id,
                    student_uid=student_uid,
                )
            )
        db.flush()
        db.add(
            models.AnalysisRun(
                id=RUN_ID,
                dataset_id=DATASET_ID,
                catalog_version="cat-i",
                rule_version="b-rules-1",
                config_json={},
                status="succeeded",
                created_at=now,
                event_cutoff=5,
            )
        )
        db.flush()
        dataset = db.get(models.Dataset, DATASET_ID)
        case_one = ensure_intervention(
            db,
            dataset,
            "uid-1",
            {"run_id": RUN_ID, "node_id": NODE_ID},
            NODE_ID,
            "no_improvement_after_trials",
            {
                "reason": "三轮有效教学仍无改善",
                "last_cutoff": 3,
                "evidence_refs": [
                    {"kind": "attempt", "id": "attempt-1", "dataset_id": DATASET_ID}
                ],
                "observed_effects": ["两次练习仍混淆符号"],
                "unresolved_question": "学生是否会分配负号？",
                "suggested_actions": ["观察负号分配过程"],
            },
        )
        case_two = ensure_intervention(
            db,
            dataset,
            "uid-2",
            {"run_id": RUN_ID, "node_id": NODE_ID},
            NODE_ID,
            "objective_safety",
            {"reason": "存在客观安全冲突", "last_cutoff": 1},
        )
        case_one_id = case_one.case_id
        case_two_id = case_two.case_id
    return {
        "dataset_id": DATASET_ID,
        "run_id": RUN_ID,
        "case_one": case_one_id,
        "case_two": case_two_id,
    }


def _session(client: TestClient, role: str, **payload) -> dict[str, str]:
    body = {"role": role, "dataset_id": DATASET_ID, **payload}
    response = client.post("/api/v1/demo-sessions", json=body)
    assert response.status_code == 201, response.text
    token = response.json()["data"]["session_token"]
    return {"Authorization": f"Bearer {token}"}


def _url(case_id: str, student_id: str = "S01") -> str:
    return f"/api/v1/runs/{RUN_ID}/students/{student_id}/interventions/{case_id}"


def _resolve_url(case_id: str, student_id: str = "S01") -> str:
    return f"{_url(case_id, student_id)}/resolve"


def test_read_open_case(client: TestClient, app_env: Path) -> None:
    world = _build_world()
    headers = _session(client, "teacher", class_ids=["C1"])
    response = client.get(
        _url(world["case_one"]), headers=headers, params={"node_id": NODE_ID}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["case_id"] == world["case_one"]
    assert data["status"] == "open"
    assert data["reason_code"] == "no_improvement_after_trials"
    assert data["target_key"] == NODE_ID
    assert data["student_id"] == "S01"
    assert data["last_cutoff"] == 3
    assert data["handling_history"] == []
    assert data["scope"]["run_id"] == RUN_ID
    assert data["scope"]["node_id"] == NODE_ID


def test_resolve_closes_case_and_writes_audit(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = {
        **_session(client, "teacher", class_ids=["C1"]),
        "Idempotency-Key": "resolve-case-one",
    }
    response = client.post(
        _resolve_url(world["case_one"]),
        headers=headers,
        json={
            "node_id": NODE_ID,
            "expected_last_cutoff": 3,
            "reason": "与家长确认后已解除阻塞",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "resolved"
    assert data["resolved_at"]
    assert len(data["handling_history"]) == 1
    history = data["handling_history"][0]
    assert history["action"] == "resolved"
    assert history["reason"] == "与家长确认后已解除阻塞"
    assert history["actor_ref"]

    reopened = client.get(
        _url(world["case_one"]), headers=headers, params={"node_id": NODE_ID}
    )
    assert reopened.status_code == 200
    assert reopened.json()["data"]["status"] == "resolved"

    with get_sessionmaker()() as db:
        row = db.get(models.InterventionRow, world["case_one"])
        assert row.status == "resolved"
        assert row.resolved_at
        audits = db.scalars(
            select(models.SystemAudit).where(
                models.SystemAudit.subject_ref == world["case_one"],
                models.SystemAudit.action == "intervention_resolved",
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].details_json["reason"] == "与家长确认后已解除阻塞"
        assert audits[0].details_json["previous_status"] == "open"
        assert audits[0].details_json["status"] == "resolved"
        assert audits[0].actor_ref
        # 结案不动评分、学情与已发布方案
        assert db.scalar(select(models.Version)) is None
        assert db.scalar(select(models.AttemptRow)) is None
        assert db.scalar(select(models.Grade)) is None
        assert db.get(models.AnalysisRun, RUN_ID).status == "succeeded"


def test_same_idempotency_key_returns_original_receipt(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = {
        **_session(client, "teacher", class_ids=["C1"]),
        "Idempotency-Key": "resolve-replay",
    }
    body = {
        "node_id": NODE_ID,
        "expected_last_cutoff": 3,
        "reason": "结案回执重放",
    }
    first = client.post(_resolve_url(world["case_one"]), headers=headers, json=body)
    assert first.status_code == 200, first.text
    replay = client.post(_resolve_url(world["case_one"]), headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert len(replay.json()["data"]["handling_history"]) == 1

    conflict = client.post(
        _resolve_url(world["case_one"]),
        headers=headers,
        json={**body, "reason": "同键不同请求"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_second_resolve_with_new_key_conflicts(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    teacher = _session(client, "teacher", class_ids=["C1"])
    body = {"node_id": NODE_ID, "expected_last_cutoff": 3, "reason": "第一次结案"}
    first = client.post(
        _resolve_url(world["case_one"]),
        headers={**teacher, "Idempotency-Key": "resolve-first"},
        json=body,
    )
    assert first.status_code == 200, first.text
    second = client.post(
        _resolve_url(world["case_one"]),
        headers={**teacher, "Idempotency-Key": "resolve-second"},
        json={**body, "reason": "第二次结案"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


def test_expected_last_cutoff_mismatch_reports_current_value(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _session(client, "teacher", class_ids=["C1"])
    response = client.post(
        _resolve_url(world["case_one"]),
        headers=headers,
        json={"node_id": NODE_ID, "expected_last_cutoff": 1, "reason": "水位过期"},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "REVISION_CONFLICT"
    assert any("3" in item["reason"] for item in error["details"])

    with get_sessionmaker()() as db:
        assert db.get(models.InterventionRow, world["case_one"]).status == "open"


def test_cross_class_is_forbidden(client: TestClient, app_env: Path) -> None:
    world = _build_world()
    headers = _session(client, "teacher", class_ids=["C1"])
    response = client.get(
        _url(world["case_two"], student_id="S02"),
        headers=headers,
        params={"node_id": NODE_ID},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_cross_student_is_forbidden(client: TestClient, app_env: Path) -> None:
    world = _build_world()
    headers = _session(client, "teacher", class_ids=["C1", "C2"])
    response = client.get(
        _url(world["case_one"], student_id="S02"),
        headers=headers,
        params={"node_id": NODE_ID},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_node_mismatch_is_context_mismatch(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _session(client, "teacher", class_ids=["C1"])
    response = client.get(
        _url(world["case_one"]), headers=headers, params={"node_id": "N99"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CONTEXT_MISMATCH"


def test_missing_case_is_not_found(client: TestClient, app_env: Path) -> None:
    _build_world()
    headers = _session(client, "teacher", class_ids=["C1"])
    response = client.get(
        _url("case-missing"), headers=headers, params={"node_id": NODE_ID}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_student_cannot_resolve(client: TestClient, app_env: Path) -> None:
    world = _build_world()
    headers = _session(client, "student", student_id="S01")
    response = client.post(
        _resolve_url(world["case_one"]),
        headers=headers,
        json={"node_id": NODE_ID, "expected_last_cutoff": 3, "reason": "学生结案"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_ensure_intervention_upserts_open_case(app_env: Path) -> None:
    world = _build_world()
    with session_scope() as db:
        dataset = db.get(models.Dataset, world["dataset_id"])
        first = ensure_intervention(
            db,
            dataset,
            "uid-1",
            {"run_id": RUN_ID, "node_id": NODE_ID},
            NODE_ID,
            "no_improvement_after_trials",
            {"reason": "补充证据", "last_cutoff": 2, "evidence_refs": [
                {"kind": "attempt", "id": "attempt-2", "dataset_id": DATASET_ID}
            ]},
        )
        assert first.case_id == world["case_one"]
        merged = first.payload_json["evidence_refs"]
        assert {item["id"] for item in merged} == {"attempt-1", "attempt-2"}
        assert first.last_cutoff == 3
        assert first.status == "open"
        assert (
            db.scalar(
                select(models.InterventionRow).where(
                    models.InterventionRow.case_id == world["case_one"]
                )
            ).case_id
            == world["case_one"]
        )

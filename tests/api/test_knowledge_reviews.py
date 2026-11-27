"""A 知识审核写入：approve/reject/edit、CAS 发布、冲突与审计（02 §9、01 §9.2）。

全部为合成数据：直接写入候选 ``versions`` 行与 ``reviews`` 待审项，不依赖 A 构建
任务，也不修改 ``pipeline.py``。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.db.session import get_sessionmaker, session_scope
from study_companion.knowledge import review_service

DATASET_ID = "ds-k"
FAMILY_ID = "fam-k"
CANDIDATE_VERSION = "kn-cand"


def _candidate_payload() -> dict:
    points = [
        {
            "kp_id": "kp-1",
            "name": "有理数加法",
            "aliases": ["同号相加"],
            "kind": "concept",
            "definition": "同号两数相加，取相同符号并把绝对值相加。",
            "scope_role": "core",
            "topic_ids": ["t1"],
            "indicator_ids": ["I1"],
            "snippet": "同号相加取相同符号",
            "source_locators": [
                {
                    "source_file": "课程要求.csv",
                    "verification_status": "unverified",
                    "source_kind": "standard",
                }
            ],
            "review_info": {
                "status": "validated",
                "method": "rule",
                "checks": [
                    {"code": "SOURCE_RESOLVABLE", "passed": True, "reason": "正式课程指标"}
                ],
            },
        },
        {
            "kp_id": "kp-2",
            "name": "异号加法",
            "aliases": [],
            "kind": "concept",
            "definition": "异号两数相加，取绝对值较大者的符号。",
            "scope_role": "core",
            "topic_ids": ["t1"],
            "indicator_ids": ["I2"],
            "snippet": "",
            "source_locators": [],
            "review_info": {
                "status": "needs_review",
                "method": "rule",
                "checks": [
                    {"code": "POINT_SCOPE", "passed": False, "reason": "缺少教材出处"}
                ],
            },
        },
    ]
    relations = [
        {
            "relation_id": "rel-1",
            "type": "prerequisite",
            "source_ids": ["kp-1"],
            "target_id": "kp-2",
            "basis": "standard",
            "note": "",
            "review_info": {"status": "needs_review", "method": "rule", "checks": []},
        }
    ]
    annotations = [
        {
            "annotation_id": "ann-1",
            "source_type": "material",
            "source_id": "Q01",
            "target_type": "knowledge_point",
            "target_ids": ["kp-2"],
            "knowledge_role": "primary",
            "basis": "standard",
            "evidence": "课程要求表",
            "review_info": {"status": "needs_review", "method": "rule", "checks": []},
        }
    ]
    review_summary = {
        "dataset_id": DATASET_ID,
        "catalog_version": "cat-k",
        "knowledge_version": CANDIDATE_VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "counts": {
            "formal_points": 2,
            "formal_relations": 1,
            "formal_annotations": 1,
            "unresolved_items": 3,
        },
        "coverage": [],
        "audit": {
            "reviewed_objects": 4,
            "first_pass": 1,
            "auto_repaired_pass": 0,
            "human_pass": 0,
            "rejected": 0,
            "needs_review": 3,
            "not_processed": 0,
        },
        "issues": [],
        "limitations": [],
    }
    return {
        "dataset_id": DATASET_ID,
        "catalog_version": "cat-k",
        "knowledge_version": CANDIDATE_VERSION,
        "parent_version": None,
        "points": points,
        "relations": relations,
        "annotations": annotations,
        "review_summary": review_summary,
        "limitations": [],
    }


def _build_world() -> dict:
    """合成数据集、候选知识版本与三个待审对象。"""

    now = repo.now_iso()
    payload = _candidate_payload()
    with session_scope() as db:
        db.add(
            models.DatasetFamily(
                family_id=FAMILY_ID,
                label="合成知识系列",
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
        db.add(models.Student(dataset_id=DATASET_ID, student_id="S01", class_id="C1"))
        db.flush()
        repo.create_version(
            db,
            kind="knowledge",
            key=repo.FormalKey(
                family_id=FAMILY_ID, dataset_id=DATASET_ID, kind="knowledge"
            ),
            payload=payload,
            rule_version="a-rules-1",
            input_digest=repo.digest(payload),
            version_id=CANDIDATE_VERSION,
            origin_kind="system",
        )
        reviews = {
            object_type: review_service.ensure_review_item(
                db, DATASET_ID, CANDIDATE_VERSION, object_type, object_id, why
            ).review_id
            for object_type, object_id, why in (
                ("point", "kp-2", "程序校验未通过：缺少教材出处"),
                ("relation", "rel-1", "关系方向待教师确认"),
                ("annotation", "ann-1", "归属角色待教师确认"),
            )
        }
    return {
        "dataset_id": DATASET_ID,
        "candidate_version": CANDIDATE_VERSION,
        "reviews": reviews,
    }


def _teacher(client: TestClient, dataset_id: str = DATASET_ID) -> dict[str, str]:
    response = client.post(
        "/api/v1/demo-sessions", json={"role": "teacher", "dataset_id": dataset_id}
    )
    assert response.status_code == 201, response.text
    token = response.json()["data"]["session_token"]
    return {"Authorization": f"Bearer {token}"}


def _student(client: TestClient, student_id: str = "S01") -> dict[str, str]:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": DATASET_ID, "student_id": student_id},
    )
    assert response.status_code == 201, response.text
    token = response.json()["data"]["session_token"]
    return {"Authorization": f"Bearer {token}"}


def _review_url(review_id: str) -> str:
    return f"/api/v1/datasets/{DATASET_ID}/knowledge/reviews/{review_id}"


def test_approve_publishes_new_version_and_moves_formal_head(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])

    listed = client.get(
        f"/api/v1/datasets/{DATASET_ID}/knowledge/reviews", headers=headers
    )
    assert listed.status_code == 200, listed.text
    listed_ids = {item["review_id"] for item in listed.json()["data"]["items"]}
    assert set(world["reviews"].values()) <= listed_ids

    review_id = world["reviews"]["point"]
    response = client.post(
        _review_url(review_id),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "已与教材第 3 页核对定义",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["review_status"] == "human_confirmed"
    assert data["published"] is True
    new_version = data["knowledge_version"]
    assert new_version and new_version != CANDIDATE_VERSION

    knowledge = client.get(f"/api/v1/datasets/{DATASET_ID}/knowledge", headers=headers)
    assert knowledge.status_code == 200, knowledge.text
    snapshot = knowledge.json()["data"]
    assert snapshot["knowledge_version"] == new_version
    assert snapshot["parent_version"] == CANDIDATE_VERSION
    assert [point["kp_id"] for point in snapshot["points"]] == ["kp-1", "kp-2"]
    approved = snapshot["points"][1]
    assert approved["review_info"]["status"] == "human_confirmed"
    assert approved["review_info"]["method"] == "human"
    assert snapshot["relations"] == []
    assert snapshot["annotations"] == []
    counts = snapshot["review_summary"]["counts"]
    audit = snapshot["review_summary"]["audit"]
    assert counts["formal_points"] == 2
    assert counts["formal_relations"] == 0
    assert counts["unresolved_items"] == 2
    assert audit["human_pass"] == 1
    assert audit["reviewed_objects"] == 4
    assert audit["needs_review"] == 2

    with get_sessionmaker()() as db:
        head = db.get(models.FormalHead, (FAMILY_ID, DATASET_ID, "knowledge", "", "", ""))
        assert head is not None
        assert head.version_id == new_version
        assert head.generation == 1
        publication = db.scalar(
            select(models.Publication).where(models.Publication.version_id == new_version)
        )
        assert publication is not None
        assert publication.review_id == review_id
        assert publication.path == "teacher_direct"
        assert publication.basis == "teacher_confirmation"
        row = db.get(models.Review, review_id)
        assert row.status == "human_confirmed"
        assert row.method == "human"
        assert row.actor_ref
        audits = db.scalars(
            select(models.SystemAudit).where(
                models.SystemAudit.action == "knowledge_review_approve"
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].before_ref == CANDIDATE_VERSION
        assert audits[0].after_ref == new_version
        assert audits[0].details_json["object_id"] == "kp-2"
        assert audits[0].details_json["reason"] == "已与教材第 3 页核对定义"


def test_reject_keeps_formal_pointer_untouched(client: TestClient, app_env: Path) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])
    review_id = world["reviews"]["relation"]

    response = client.post(
        _review_url(review_id),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "reject",
            "reason": "关系方向与教材表述相反",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["review_status"] == "rejected"
    assert data["published"] is False
    assert data["knowledge_version"] is None

    knowledge = client.get(f"/api/v1/datasets/{DATASET_ID}/knowledge", headers=headers)
    assert knowledge.status_code == 409
    assert knowledge.json()["error"]["code"] == "RESULT_NOT_READY"

    with get_sessionmaker()() as db:
        assert db.scalar(select(models.FormalHead)) is None
        assert db.scalar(select(models.Publication)) is None
        versions = db.scalars(select(models.Version)).all()
        assert [row.version_id for row in versions] == [CANDIDATE_VERSION]
        row = db.get(models.Review, review_id)
        assert row.status == "rejected"
        assert row.issues_json[-1]["code"] == "HUMAN_REJECTED"
        audits = db.scalars(
            select(models.SystemAudit).where(
                models.SystemAudit.action == "knowledge_review_reject"
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].details_json["formal_unchanged"] is True


def test_edit_creates_new_candidate_without_publishing(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])
    review_id = world["reviews"]["annotation"]

    response = client.post(
        _review_url(review_id),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "edit",
            "changes": {"target_ids": ["kp-1"], "knowledge_role": "auxiliary"},
            "reason": "该题主要考查同号加法，归属改为辅助",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["review_status"] == "needs_review"
    assert data["published"] is False
    assert data["knowledge_version"] is None
    new_candidate = data["candidate_version"]
    assert new_candidate != CANDIDATE_VERSION
    assert data["previous_candidate_version"] == CANDIDATE_VERSION

    knowledge = client.get(f"/api/v1/datasets/{DATASET_ID}/knowledge", headers=headers)
    assert knowledge.status_code == 409

    with get_sessionmaker()() as db:
        version = db.get(models.Version, new_candidate)
        assert version is not None
        assert version.kind == "knowledge"
        assert version.payload_json["parent_version"] == CANDIDATE_VERSION
        annotation = version.payload_json["annotations"][0]
        assert annotation["annotation_id"] == "ann-1"
        assert annotation["target_ids"] == ["kp-1"]
        assert annotation["knowledge_role"] == "auxiliary"
        assert db.scalar(select(models.Publication)) is None
        row = db.get(models.Review, review_id)
        assert row.status == "needs_review"
        pending = review_service.list_pending(db, DATASET_ID)
        edited = next(item for item in pending if item["review_id"] == review_id)
        assert edited["candidate_version"] == new_candidate
        assert edited["object_id"] == "ann-1"

    stale = client.post(
        _review_url(review_id),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "仍按旧候选批准",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"


def test_base_candidate_version_conflict_returns_409(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])
    review_id = world["reviews"]["point"]

    response = client.post(
        _review_url(review_id),
        headers=headers,
        json={
            "base_candidate_version": "kn-older",
            "decision": "approve",
            "reason": "基于旧候选的批准",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"

    with get_sessionmaker()() as db:
        assert db.get(models.Review, review_id).status == "needs_review"
        assert db.scalar(select(models.FormalHead)) is None
        assert [row.version_id for row in db.scalars(select(models.Version))] == [
            CANDIDATE_VERSION
        ]


def test_second_approval_keeps_previous_formal_objects(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])
    first = client.post(
        _review_url(world["reviews"]["point"]),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "知识点定义已核对",
        },
    )
    assert first.status_code == 200, first.text
    first_version = first.json()["data"]["knowledge_version"]

    second = client.post(
        _review_url(world["reviews"]["relation"]),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "前置关系方向已确认",
        },
    )
    assert second.status_code == 200, second.text
    second_version = second.json()["data"]["knowledge_version"]
    assert second_version != first_version

    snapshot = client.get(
        f"/api/v1/datasets/{DATASET_ID}/knowledge", headers=headers
    ).json()["data"]
    assert snapshot["knowledge_version"] == second_version
    assert [point["kp_id"] for point in snapshot["points"]] == ["kp-1", "kp-2"]
    assert [relation["relation_id"] for relation in snapshot["relations"]] == ["rel-1"]
    assert snapshot["annotations"] == []
    assert snapshot["review_summary"]["counts"]["unresolved_items"] == 1

    with get_sessionmaker()() as db:
        head = db.get(models.FormalHead, (FAMILY_ID, DATASET_ID, "knowledge", "", "", ""))
        assert head.version_id == second_version
        assert head.generation == 2
        publication = db.scalar(
            select(models.Publication).where(
                models.Publication.version_id == second_version
            )
        )
        assert publication.previous_version_id == first_version


def test_approve_works_for_review_row_written_directly(
    client: TestClient, app_env: Path
) -> None:
    _build_world()
    with session_scope() as db:
        db.add(
            models.Review(
                review_id="krev-manual",
                subject_kind="point",
                subject_key=f"{DATASET_ID}:kp-2",
                subject_hash="",
                status="needs_review",
                method="rule",
                checks_json=[],
                issues_json=[],
                created_at=repo.now_iso(),
            )
        )
    headers = _teacher(client)
    response = client.post(
        _review_url("krev-manual"),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "按请求声明的候选版本批准",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["published"] is True
    snapshot = client.get(
        f"/api/v1/datasets/{DATASET_ID}/knowledge", headers=headers
    ).json()["data"]
    assert [point["kp_id"] for point in snapshot["points"]] == ["kp-1", "kp-2"]


def test_edit_rejects_field_outside_whitelist(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _teacher(client, world["dataset_id"])
    response = client.post(
        _review_url(world["reviews"]["annotation"]),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "edit",
            "changes": {"annotation_id": "ann-2"},
            "reason": "尝试修改对象 ID",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_review_is_not_found(client: TestClient, app_env: Path) -> None:
    _build_world()
    headers = _teacher(client)
    response = client.post(
        _review_url("krev-missing"),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "不存在的审核对象",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_student_cannot_decide_knowledge_review(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = _student(client)
    response = client.post(
        _review_url(world["reviews"]["point"]),
        headers=headers,
        json={
            "base_candidate_version": CANDIDATE_VERSION,
            "decision": "approve",
            "reason": "学生尝试审核",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_decide_is_idempotent_and_audited_once(
    client: TestClient, app_env: Path
) -> None:
    world = _build_world()
    headers = {**_teacher(client, world["dataset_id"]), "Idempotency-Key": "review-approve-1"}
    review_id = world["reviews"]["point"]
    body = {
        "base_candidate_version": CANDIDATE_VERSION,
        "decision": "approve",
        "reason": "定义已核对",
    }

    first = client.post(_review_url(review_id), headers=headers, json=body)
    assert first.status_code == 200, first.text
    replay = client.post(_review_url(review_id), headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]

    conflict = client.post(
        _review_url(review_id),
        headers=headers,
        json={**body, "reason": "同键不同请求"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    with get_sessionmaker()() as db:
        assert len(db.scalars(select(models.Publication)).all()) == 1
        assert (
            len(
                db.scalars(
                    select(models.SystemAudit).where(
                        models.SystemAudit.action == "knowledge_review_approve"
                    )
                ).all()
            )
            == 1
        )
        head = db.get(models.FormalHead, (FAMILY_ID, DATASET_ID, "knowledge", "", "", ""))
        assert head.version_id == first.json()["data"]["knowledge_version"]
        assert head.generation == 1

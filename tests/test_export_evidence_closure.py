"""证据闭包回归：分页上限、原始作答投影与记录引用业务校验。

全部使用六表合成 fixture；真实数据重建属于 REL-02，不在本文件执行。
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.exports import service
from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")
EXTRA_EVENTS = 620


def _teacher(client: TestClient, dataset_id: str | None = None) -> dict[str, str]:
    payload: dict[str, Any] = {"role": "teacher"}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    token = client.post("/api/v1/demo-sessions", json=payload).json()["data"][
        "session_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _filtered(name: str, students: tuple[str, ...], nodes: tuple[str, ...]) -> tuple[str, bytes, str]:
    rows = list(
        csv.reader(io.StringIO((FIXTURES / f"{name}.csv").read_text(encoding="utf-8-sig")))
    )
    header, body = rows[0], rows[1:]
    if name == "students":
        body = [row for row in body if row[0] in students]
    elif name == "nodes":
        body = [row for row in body if row[0] in nodes]
    elif name == "records":
        body = [row for row in body if row[1] in students and row[4] in nodes]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(body)
    return (f"{name}.csv", buffer.getvalue().encode("utf-8"), "text/csv")


def _import(
    client: TestClient,
    *,
    students: tuple[str, ...] = ("S01", "S02"),
    nodes: tuple[str, ...] = ("N01",),
) -> dict:
    files = [("files", _filtered(name, students, nodes)) for name in TABLES]
    receipt = client.post(
        "/api/v1/imports",
        headers=_teacher(client),
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    ).json()["data"]
    drain()
    return receipt


def _run_id(client: TestClient, dataset_id: str, headers: dict[str, str]) -> str:
    return client.get(
        f"/api/v1/datasets/{dataset_id}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]


def _frozen_export(
    client: TestClient, session: Session
) -> tuple[models.Export, models.Dataset, dict[str, str], str]:
    receipt = _import(client)
    dataset = session.get(models.Dataset, receipt["dataset_id"])
    assert dataset is not None
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    response = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert response.status_code == 202, response.text
    export_id = response.json()["data"]["export_id"]
    assert drain() >= 1
    frozen = session.get(models.Export, export_id)
    assert frozen is not None
    return frozen, dataset, headers, run_id


def _items(files: dict[str, bytes], name: str) -> list[dict[str, Any]]:
    return json.loads(files[name].decode("utf-8"))["items"]


def _current_student_version(
    session: Session, dataset: models.Dataset, run_id: str, student_id: str = "S01"
) -> models.Version:
    version = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="student_analysis",
            run_key=run_id,
            node_key="N01",
            target_key=student_id,
        ),
    )
    assert version is not None
    return version


def _add_source_row(
    session: Session,
    dataset: models.Dataset,
    *,
    row_number: int,
    payload: dict[str, Any],
) -> models.SourceRow:
    row_id = f"sr-evidence-{row_number}"
    row = models.SourceRow(
        id=row_id,
        dataset_id=dataset.id,
        table_name="records",
        row_number=row_number,
        payload_json=payload,
        sheet_key="",
        physical_row=row_number,
    )
    session.add(row)
    session.flush()
    return row


def _add_answer_event(
    session: Session,
    dataset: models.Dataset,
    student: models.Student,
    *,
    index: int,
    record_id: str,
    source_row: models.SourceRow,
    solution: str,
) -> None:
    payload = {
        "event_id": f"evx-{index:04d}",
        "student_id": student.student_id,
        "node_id": "N01",
        "material_id": "Q1",
        "selected_record_id": record_id,
        "status": "graded",
        "score": 3,
        "max_score": 5,
    }
    session.add(
        models.AnswerEvent(
            dataset_id=dataset.id,
            event_id=f"evx-{index:04d}",
            student_id=student.student_id,
            node_id="N01",
            material_id="Q1",
            selected_source_row_id=source_row.id,
            disposition="include",
            payload_json=payload,
            raw_row_id=source_row.id,
            student_uid=student.student_uid,
            source_event_key=f"evx-{index:04d}",
            source_revision=1,
        )
    )
    source_row.payload_json = {
        "记录编号": record_id,
        "学生编号": student.student_id,
        "材料编号": "Q1",
        "节点编号": "N01",
        "评分状态": "已评分",
        "得分": "3",
        "满分": "5",
        "学生最终作答": f"答案 {index}",
        "解答过程": solution,
        "评分点得分": "Q1P1=1；Q1P2=0",
        # 参考答案不在安全投影白名单内，必须留在原始行里而不进入证据。
        "参考答案": f"SECRET-REFERENCE-{index}",
        "隐藏提示": f"SECRET-HINT-{index}",
    }


def _extend_with_more_than_500_events(
    session: Session, dataset: models.Dataset
) -> list[str]:
    s01 = session.get(models.Student, (dataset.id, "S01"))
    s02 = session.get(models.Student, (dataset.id, "S02"))
    assert s01 is not None and s02 is not None
    record_ids: list[str] = []
    for index in range(EXTRA_EVENTS):
        record_id = f"DRX{index:04d}"
        record_ids.append(record_id)
        row = _add_source_row(
            session,
            dataset,
            row_number=1000 + index,
            payload={},
        )
        student = s01 if index % 2 == 0 else s02
        _add_answer_event(
            session,
            dataset,
            student,
            index=index,
            record_id=record_id,
            source_row=row,
            solution=f"第{index}步：先化简\n第{index}步：再代入",
        )
    session.commit()
    return record_ids


def _append_metric_ref(
    session: Session,
    version: models.Version,
    record_id: str,
    *,
    metric_id: str,
) -> None:
    payload = json.loads(json.dumps(version.payload_json))
    metrics = list(payload.get("metrics") or [])
    metrics.append(
        {
            "metric_id": metric_id,
            "name": "record_ref_probe",
            "value": None,
            "numerator": None,
            "denominator": None,
            "unit": "count",
            "scope": {"student_id": "S01", "node_id": "N01"},
            "evidence_refs": [{"kind": "record", "id": record_id}],
            "limitations": [],
        }
    )
    payload["metrics"] = metrics
    version.payload_json = payload
    session.commit()


# --------------------------------------------------------------------------- #
# 证据闭包与原始作答投影
# --------------------------------------------------------------------------- #


def test_evidence_closure_exports_all_answer_events_and_resolves_refs(
    client: TestClient, session: Session
) -> None:
    frozen, dataset, _headers, run_id = _frozen_export(client, session)
    record_ids = _extend_with_more_than_500_events(session, dataset)

    # 只存在原始记录、没有 AnswerEvent 的引用也必须补查 SourceRow 后导出。
    fallback_id = "DRX-FALLBACK-ONLY"
    fallback_row = _add_source_row(
        session,
        dataset,
        row_number=2000,
        payload={
            "记录编号": fallback_id,
            "学生编号": "S01",
            "材料编号": "Q1",
            "节点编号": "N01",
            "评分状态": "已评分",
            "得分": "4",
            "满分": "5",
            "学生最终作答": "fallback answer",
            "解答过程": "fallback step 1\nfallback step 2",
            "评分点得分": "Q1P1=1；Q1P2=1",
            "参考答案": "SECRET-FALLBACK",
        },
    )
    assert fallback_row is not None
    version = _current_student_version(session, dataset, run_id)
    _append_metric_ref(session, version, fallback_id, metric_id="m-fallback-probe")

    files = service.build_files(session, frozen)
    evidence = _items(files, "evidence_records.json")
    evidence_ids = {item["ref"]["id"] for item in evidence}
    assert len(evidence) > 500, "证据闭包不能停在 500 条展示分页"
    assert set(record_ids) <= evidence_ids
    assert fallback_id in evidence_ids, "被 metric 引用的 SourceRow 必须补查导出"

    probe = next(item for item in evidence if item["ref"]["id"] == "DRX0000")
    detail = probe["detail"]
    assert detail["student_answer"] == "答案 0"
    assert detail["solution_steps"] == "第0步：先化简\n第0步：再代入"
    assert detail["rubric_scores"] == [
        {"rubric_id": "Q1P1", "awarded": 1},
        {"rubric_id": "Q1P2", "awarded": 0},
    ]
    assert detail["source_row_number"] == 1000
    assert detail["question"]["material_id"] == "Q1"
    serialized = json.dumps(probe, ensure_ascii=False)
    assert "SECRET-REFERENCE" not in serialized
    assert "SECRET-HINT" not in serialized
    assert "参考答案" not in serialized and "隐藏提示" not in serialized

    fallback = next(item for item in evidence if item["ref"]["id"] == fallback_id)
    assert fallback["detail"]["solution_steps"] == "fallback step 1\nfallback step 2"
    assert fallback["detail"]["source_row_number"] == 2000

    # 所有 metric/finding 的记录引用都要能逐项解析，而不是只校验文件 SHA。
    record_refs: set[str] = set()
    for name in ("student_analysis.json", "class_analysis.json"):
        for record in _items(files, name):
            record_refs.update(service._record_refs_in(record.get("result") or {}))
    assert record_refs
    assert record_refs <= evidence_ids

    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    checks = manifest["content_checks"]
    assert checks["records"] == len(evidence)
    assert checks["records"] >= EXTRA_EVENTS
    assert checks["records_covered_students"] >= 2
    assert checks["records_with_source_row"] >= EXTRA_EVENTS
    assert checks["records_with_student_answer"] >= EXTRA_EVENTS
    assert checks["record_refs_total"] > 0
    assert checks["record_refs_unique"] > 0
    assert checks["record_refs_missing"] == 0
    assert checks["record_refs_missing_ids"] == []
    assert not any(
        "无法在 evidence_records.json 中解析" in issue
        for issue in manifest["validation"]["content_issues"]
    )


# --------------------------------------------------------------------------- #
# 缺失引用必须被内容检查捕获
# --------------------------------------------------------------------------- #


def test_content_check_catches_unresolvable_record_refs(
    client: TestClient, session: Session, tmp_path: Path
) -> None:
    frozen, dataset, _headers, run_id = _frozen_export(client, session)
    version = _current_student_version(session, dataset, run_id)
    missing_id = "DRX-NOT-IN-SOURCE"
    _append_metric_ref(session, version, missing_id, metric_id="m-missing-probe")

    result = service.build_archive(session, frozen, tmp_path)
    manifest = result["manifest"]
    checks = manifest["content_checks"]
    assert checks["record_refs_missing"] == 1
    assert checks["record_refs_missing_ids"] == [missing_id]
    assert any(
        "1 个 metric/finding 记录引用无法在 evidence_records.json 中解析" in issue
        for issue in manifest["validation"]["content_issues"]
    )
    assert any("记录引用解析" in item for item in manifest["limitations"])
    assert result["check_report"]["content_status"] == "passed_with_limitations"
    assert any(
        "无法在 evidence_records.json 中解析"
        for item in result["check_report"]["content_issues"]
    )

    archive_path = tmp_path / "exports" / f"{frozen.export_id}.zip"
    assert archive_path.is_file() and archive_path.stat().st_size > 0
    with zipfile.ZipFile(archive_path) as archive:
        packaged = json.loads(archive.read("manifest.json"))
    assert packaged["content_checks"]["record_refs_missing"] == 1
    assert packaged["validation"]["content_issues"] == manifest["validation"][
        "content_issues"
    ]


# --------------------------------------------------------------------------- #
# 缺失原文字段不伪造
# --------------------------------------------------------------------------- #


def test_learning_history_is_not_capped_at_500(
    client: TestClient, session: Session
) -> None:
    frozen, dataset, _headers, run_id = _frozen_export(client, session)
    student = session.get(models.Student, (dataset.id, "S01"))
    assert student is not None and student.student_uid
    for index in range(EXTRA_EVENTS):
        session.add(
            models.LearningEventRow(
                event_id=f"lev-{index:04d}",
                family_id=dataset.family_id or "",
                dataset_id=dataset.id,
                run_id=run_id,
                student_uid=student.student_uid,
                node_id="N01",
                plan_version=None,
                group_id=None,
                activity_id=None,
                material_id="Q1",
                attempt_id=None,
                scope_key=f"scope-{index:04d}",
                sequence=1,
                event_type="answer",
                source="online",
                payload_json={"index": index},
                occurred_at="2020-01-01T00:00:00+00:00",
                business_key=f"business-{index:04d}",
            )
        )
    session.commit()

    files = service.build_files(session, frozen)
    learning = _items(files, "learning_history.json")
    exported = {item["data"]["event_id"] for item in learning}
    assert len(learning) >= EXTRA_EVENTS
    assert {f"lev-{index:04d}" for index in range(EXTRA_EVENTS)} <= exported


def test_evidence_detail_keeps_missing_fields_null(
    client: TestClient, session: Session
) -> None:
    frozen, dataset, _headers, run_id = _frozen_export(client, session)
    record_id = "DRX-NO-TEXT"
    _add_source_row(
        session,
        dataset,
        row_number=3000,
        payload={
            "记录编号": record_id,
            "学生编号": "S01",
            "材料编号": "Q1",
            "节点编号": "N01",
            "评分状态": "待评分",
        },
    )
    version = _current_student_version(session, dataset, run_id)
    _append_metric_ref(session, version, record_id, metric_id="m-notext-probe")

    files = service.build_files(session, frozen)
    evidence = _items(files, "evidence_records.json")
    probe = next(item for item in evidence if item["ref"]["id"] == record_id)
    assert probe["detail"]["student_answer"] is None
    assert probe["detail"]["solution_steps"] is None
    assert probe["detail"]["rubric_scores"] == []
    assert probe["detail"]["source_row_number"] == 3000

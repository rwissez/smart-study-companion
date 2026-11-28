"""PDF 来源确认必须持久化，教材抽取只能读取教材文件。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db.session import get_sessionmaker
from study_companion.pipeline import _textbook_candidates


FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _teacher_token(client: TestClient) -> str:
    response = client.post("/api/v1/demo-sessions", json={"role": "teacher"})
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def test_confirmed_pdf_roles_are_persisted_and_only_textbooks_are_extracted(
    client: TestClient, monkeypatch
) -> None:
    """若删除角色回写或教材角色过滤，本测试会分别读到 standard 或两个 PDF。"""

    headers = {"Authorization": f"Bearer {_teacher_token(client)}"}
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    files.extend(
        [
            ("files", ("课程标准.pdf", b"%PDF-1.4\nstandard", "application/pdf")),
            ("files", ("B01_教材切片.pdf", b"%PDF-1.4\ntextbook", "application/pdf")),
        ]
    )
    created = client.post(
        "/api/v1/imports",
        headers=headers,
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    )
    assert created.status_code == 202, created.text
    receipt = created.json()["data"]
    assert receipt["classification_status"] == "needs_confirmation"

    pdfs = {item["original_name"]: item for item in receipt["files"] if item["format"] == "pdf"}
    confirmed = client.post(
        f"/api/v1/imports/{receipt['import_id']}/confirm",
        headers=headers,
        json={
            "intent": "new_family",
            "file_mappings": [
                {"file_id": pdfs["课程标准.pdf"]["file_id"], "sheet_name": None, "role": "standard"},
                {"file_id": pdfs["B01_教材切片.pdf"]["file_id"], "sheet_name": None, "role": "textbook"},
            ],
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    dataset_id = confirmed.json()["data"]["dataset_id"]
    assert dataset_id
    assert confirmed.json()["data"]["job_id"], "确认导入后必须返回已排队的导入任务"

    with get_sessionmaker()() as db:
        rows = db.execute(
            select(models.ImportFileRow.original_name, models.ImportFileRow.role).where(
                models.ImportFileRow.import_id == receipt["import_id"]
            )
        ).all()
        roles = dict(rows)
        assert roles["课程标准.pdf"] == "standard"
        assert roles["B01_教材切片.pdf"] == "textbook"

        seen: list[str] = []

        def reject_after_recording(file_id: str, _data: bytes):
            seen.append(file_id)
            raise DomainError("UNPROCESSABLE", "test stop")

        monkeypatch.setattr("study_companion.knowledge.pdf_reader.read_pdf", reject_after_recording)
        catalog = SimpleNamespace(topics=[], indicators=[], materials=[])
        candidates, stats = _textbook_candidates(db, dataset_id, catalog)

    assert seen == [pdfs["B01_教材切片.pdf"]["file_id"]]
    assert candidates == []
    assert not stats.get("model_notes")


def test_pdf_left_on_default_role_still_extracts_and_says_so(client: TestClient, monkeypatch) -> None:
    """历史与默认导入里 PDF 的角色是 standard：仍参与抽取，但在限制说明里提示。"""

    headers = {"Authorization": f"Bearer {_teacher_token(client)}"}
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    files.append(("files", ("B02_教材切片.pdf", b"%PDF-1.4\ntextbook", "application/pdf")))
    created = client.post(
        "/api/v1/imports",
        headers=headers,
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    )
    assert created.status_code == 202, created.text
    receipt = created.json()["data"]
    pdf = next(item for item in receipt["files"] if item["format"] == "pdf")

    confirmed = client.post(
        f"/api/v1/imports/{receipt['import_id']}/confirm",
        headers=headers,
        json={"intent": "new_family", "file_mappings": []},
    )
    assert confirmed.status_code == 202, confirmed.text
    dataset_id = confirmed.json()["data"]["dataset_id"]
    assert dataset_id

    seen: list[str] = []

    def reject_after_recording(file_id: str, _data: bytes):
        seen.append(file_id)
        raise DomainError("UNPROCESSABLE", "test stop")

    monkeypatch.setattr("study_companion.knowledge.pdf_reader.read_pdf", reject_after_recording)
    with get_sessionmaker()() as db:
        catalog = SimpleNamespace(topics=[], indicators=[], materials=[])
        _candidates, stats = _textbook_candidates(db, dataset_id, catalog)

    assert seen == [pdf["file_id"]]
    notes = " ".join(stats.get("model_notes") or [])
    assert "没有标注为教材" in notes

"""受控来源页读取 ``GET /sources/{file_id}/pages/{pdf_page}``（01 §9.2、02 §5）。

用真实 PDF 字节走完整 HTTP 链路：登记 FileObject + DatasetSource，再用 pypdf
检查返回的是**单页** PDF。越权与越界各有用例。
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from study_companion.config import get_settings
from study_companion.db import models
from study_companion.db.session import session_scope
from study_companion.db.version_repository import now_iso
from study_companion.library import blobs

DATASET_ID = "ds-src"
OTHER_DATASET_ID = "ds-src-other"
PAGE_ONE = "Chapter one alpha marker: the first page text layer content of the textbook."
PAGE_TWO = "Chapter two beta marker: the second page text layer content differs here."
SOURCES_PATH = "/api/v1/sources/{file_id}/pages/{pdf_page}"


@pytest.fixture
def client(app_env: Path) -> Iterator[TestClient]:
    """在现有 app 上挂 sources 路由；main.py 的注册由协调者统一添加。"""

    from study_companion.api.routes import sources
    from study_companion.main import create_app

    app = create_app()
    if not any(getattr(route, "path", "") == SOURCES_PATH for route in app.routes):
        app.include_router(sources.router, prefix="/api/v1")
    with TestClient(app) as test_client:
        yield test_client


def _text_pdf(pages: list[str]) -> bytes:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        operations = ["BT", "/F1 12 Tf", "14 TL", "72 760 Td"]
        for line in text.split("\n"):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            operations.append(f"({escaped}) Tj")
            operations.append("T*")
        operations.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(operations).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _store_file(db, data: bytes, media_type: str = "application/pdf") -> str:
    blob = blobs.store_bytes(data, get_settings())
    row = models.FileObject(
        file_id=f"file-{uuid4().hex[:10]}",
        sha256=blob.sha256,
        byte_size=blob.byte_size,
        storage_key=blob.storage_key,
        media_type=media_type,
        created_at=now_iso(),
    )
    db.add(row)
    db.flush()
    return row.file_id


def _seed() -> dict[str, str]:
    """两个数据集各登记一份 PDF，另有一份存在但未登记到任何数据集的文件。"""

    textbook_pdf = _text_pdf([PAGE_ONE, PAGE_TWO])
    other_pdf = _text_pdf(["Another dataset page contents, not for this session."])

    with session_scope() as db:
        now = now_iso()
        for dataset_id in (DATASET_ID, OTHER_DATASET_ID):
            db.add(
                models.Dataset(
                    id=dataset_id,
                    created_at=now,
                    parent_id=None,
                    status="ready",
                    family_id=None,
                    manifest_json={"synthetic": True},
                    state="ready",
                )
            )
        db.add(models.Student(dataset_id=DATASET_ID, student_id="S01", class_id="C1"))
        db.add(models.Student(dataset_id=OTHER_DATASET_ID, student_id="S02", class_id="C2"))
        for import_id, dataset_id in (
            ("imp-src", DATASET_ID),
            ("imp-src-other", OTHER_DATASET_ID),
        ):
            db.add(
                models.ImportRecord(
                    import_id=import_id,
                    family_id=None,
                    dataset_id=dataset_id,
                    expected_parent=None,
                    actor_ref="seed",
                    request_hash="",
                    intent="new_family",
                    classification_status="confirmed",
                    input_status="ready",
                    mapping_json={},
                    issues_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()

        file_id = _store_file(db, textbook_pdf)
        other_file_id = _store_file(db, other_pdf)
        unregistered_id = _store_file(db, _text_pdf(["Unregistered page content only."]))
        csv_file_id = _store_file(db, b"record_id,score\nR1,3\n", media_type="text/csv")

        db.add(
            models.DatasetSource(
                dataset_id=DATASET_ID,
                source_key="textbook",
                file_id=file_id,
                origin_import_id="imp-src",
                source_meta_json={"role": "textbook"},
            )
        )
        db.add(
            models.DatasetSource(
                dataset_id=OTHER_DATASET_ID,
                source_key="textbook",
                file_id=other_file_id,
                origin_import_id="imp-src-other",
                source_meta_json={"role": "textbook"},
            )
        )
        db.add(
            models.DatasetSource(
                dataset_id=DATASET_ID,
                source_key="records",
                file_id=csv_file_id,
                origin_import_id="imp-src",
                source_meta_json={"role": "records"},
            )
        )

    return {
        "file_id": file_id,
        "other_file_id": other_file_id,
        "unregistered_id": unregistered_id,
        "csv_file_id": csv_file_id,
    }


def _token(client: TestClient, **payload) -> str:
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _teacher(client: TestClient, dataset_id: str = DATASET_ID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token(client, role='teacher', dataset_id=dataset_id)}"
    }


def _student(client: TestClient) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {_token(client, role='student', dataset_id=DATASET_ID, student_id='S01')}"
        )
    }


def _url(file_id: str, page: int) -> str:
    return f"/api/v1/sources/{file_id}/pages/{page}"


def test_teacher_receives_only_the_requested_page(
    client: TestClient, app_env: Path
) -> None:
    world = _seed()
    response = client.get(_url(world["file_id"], 2), headers=_teacher(client))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"%PDF")

    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text() or ""
    assert "beta marker" in text
    assert "alpha marker" not in text


def test_page_out_of_range_is_404(client: TestClient, app_env: Path) -> None:
    world = _seed()
    headers = _teacher(client)
    for page in (3, 99):
        response = client.get(_url(world["file_id"], page), headers=headers)
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_student_cannot_read_source_pages(client: TestClient, app_env: Path) -> None:
    world = _seed()
    response = client.get(_url(world["file_id"], 1), headers=_student(client))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_file_of_another_dataset_is_forbidden(
    client: TestClient, app_env: Path
) -> None:
    world = _seed()
    response = client.get(_url(world["other_file_id"], 1), headers=_teacher(client))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unregistered_file_is_forbidden(client: TestClient, app_env: Path) -> None:
    world = _seed()
    response = client.get(_url(world["unregistered_id"], 1), headers=_teacher(client))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_file_is_404(client: TestClient, app_env: Path) -> None:
    _seed()
    response = client.get(_url("file-does-not-exist", 1), headers=_teacher(client))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_non_pdf_source_is_rejected(client: TestClient, app_env: Path) -> None:
    world = _seed()
    response = client.get(_url(world["csv_file_id"], 1), headers=_teacher(client))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNPROCESSABLE"


def test_missing_token_never_reads_a_page(client: TestClient, app_env: Path) -> None:
    world = _seed()
    response = client.get(_url(world["file_id"], 1))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

"""正式导出接入材料来源精度：57/8 投影、教材零页码与真实导出字段。

全部使用合成夹具；真实库只读重建在交付说明里单独记录，落在本测试文件之外。
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from study_companion.exports import service
from study_companion.jobs.worker import drain
from study_companion.sources import (
    MaterialInput,
    RawMaterialRow,
    TextbookPage,
    enrich_sources,
)

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


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


def _import(client: TestClient) -> dict:
    files = [("files", _filtered(name, ("S01", "S02"), ("N01",))) for name in TABLES]
    receipt = client.post(
        "/api/v1/imports",
        headers=_teacher(client),
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    ).json()["data"]
    drain()
    return receipt


def _export(client: TestClient, headers: dict[str, str], run_id: str) -> zipfile.ZipFile:
    response = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert response.status_code == 202, response.text
    export = response.json()["data"]
    assert drain() >= 1
    download = client.get(
        f"/api/v1/exports/{export['export_id']}/download", headers=headers
    )
    assert download.status_code == 200, download.text
    return zipfile.ZipFile(io.BytesIO(download.content))


def _items(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return json.loads(archive.read(name))["items"]


def _catalog_materials_and_rows() -> tuple[
    list[MaterialInput], list[RawMaterialRow], list[TextbookPage], dict[str, str]
]:
    """构造正式目录的 57 可验证 + 8 教材不可验证分布。"""

    materials: list[MaterialInput] = []
    rows: list[RawMaterialRow] = []
    row_number = 0
    for index in range(1, 55):
        row_number += 1
        material_id = f"Q{index:03d}"
        content = f"第 {index} 题：化简 -(2x-{index})。"
        materials.append(
            MaterialInput(
                material_id=material_id,
                material_type="question",
                content=content,
                source_locator={
                    "source_file": "赛事新编",
                    "verification_status": "unverified",
                },
                source_label="赛事新编",
            )
        )
        rows.append(
            RawMaterialRow(
                row_number=row_number,
                payload={
                    "材料编号": material_id,
                    "材料类型": "题目",
                    "题干或内容": content,
                },
                file_id="file-materials",
                source_file="题目与材料",
            )
        )
    for index in range(1, 4):
        row_number += 1
        material_id = f"L{index:02d}"
        content = f"学习卡 {index}：同底数幂相乘，底数不变，指数相加。"
        materials.append(
            MaterialInput(
                material_id=material_id,
                material_type="card",
                content=content,
                source_locator={
                    "source_file": "赛事新编",
                    "verification_status": "unverified",
                },
                source_label="赛事新编",
            )
        )
        rows.append(
            RawMaterialRow(
                row_number=row_number,
                payload={
                    "材料编号": material_id,
                    "材料类型": "学习卡",
                    "题干或内容": content,
                },
                file_id="file-materials",
                source_file="题目与材料",
            )
        )

    textbook_pages: list[TextbookPage] = []
    file_ids: dict[str, str] = {}
    for index in range(1, 9):
        material_id = f"B{index:02d}"
        content = "七年级下册 第十一章 不等式与不等式组"
        locator: dict[str, Any] = {
            "source_file": f"5_课程标准及相关教学资源/{material_id}_相关资料切片.pdf",
            "verification_status": "unverified",
            "file_id": None,
        }
        if material_id == "B02":
            locator["file_id"] = "file-b02"
            file_ids["B02_相关资料切片.pdf"] = "file-b02"
            textbook_pages.append(
                TextbookPage(file_id="file-b02", pdf_page=7, text=content)
            )
        materials.append(
            MaterialInput(
                material_id=material_id,
                material_type="textbook",
                content=content,
                source_locator=locator,
                source_label=str(locator["source_file"]),
            )
        )
    return materials, rows, textbook_pages, file_ids


def test_projection_keeps_57_verified_and_8_unverified() -> None:
    materials, rows, pages, file_ids = _catalog_materials_and_rows()
    report = enrich_sources(
        materials,
        material_rows=rows,
        textbook_pages=pages,
        file_ids=file_ids,
    )
    assert report.by_status() == {
        "verified": 57,
        "unverified": 8,
        "not_applicable": 0,
    }

    verified: list[tuple[Any, list[dict[str, Any]]]] = []
    unverified: list[tuple[Any, list[dict[str, Any]]]] = []
    for item in report.items:
        source_refs, source_status, source_note = service._project_material_source(
            item, catalog_version="catalog-test"
        )
        assert source_status == item.source_status
        assert source_note == item.reason_detail
        (verified if source_status == "verified" else unverified).append(
            (item, source_refs)
        )

    assert len(verified) == 57
    for item, source_refs in verified:
        assert len(source_refs) == 1
        ref = source_refs[0]
        assert ref["kind"] == "material_source"
        assert ref["material_id"] == item.material_id
        assert ref["verification_status"] == "verified"
        assert ref["locator_level"] == "structured"
        assert ref["source_file"] == "题目与材料"
        assert isinstance(ref["source_row_number"], int)
        assert ref["source_row_number"] >= 1
        assert ref["quote"] == item.quote
        assert ref["catalog_version"] == "catalog-test"

    assert len(unverified) == 8
    reasons = [item.reason for item, _ in unverified]
    assert reasons.count("textbook_file_not_registered") == 7
    assert reasons.count("textbook_content_not_quotable") == 1
    for item, source_refs in unverified:
        assert source_refs, f"{item.material_id} 应保留已登记的来源标签"
        ref = source_refs[0]
        assert ref["verification_status"] == "unverified"
        assert "pdf_page" not in ref, "教材未逐字命中时不能写页码"
        assert "source_row_number" not in ref
        assert "quote" not in ref
    b02 = next(item for item, _ in unverified if item.material_id == "B02")
    assert b02.file_id == "file-b02", "已登记文件要保留 file_id，但不写页码"
    b02_ref = next(ref for _, refs in unverified for ref in refs if ref["material_id"] == "B02")
    assert b02_ref["file_id"] == "file-b02"
    assert "章节标题" in b02.reason_detail
    b01 = next(item for item, _ in unverified if item.material_id == "B01")
    assert "file_id" in b01.reason_detail


def test_duplicate_material_rows_are_rejected_in_projection() -> None:
    content = "同一编号出现两次的题干"
    materials = [
        MaterialInput(
            material_id="QDUP",
            material_type="question",
            content=content,
            source_locator={
                "source_file": "赛事新编",
                "verification_status": "unverified",
            },
        )
    ]
    rows = [
        RawMaterialRow(
            row_number=1,
            payload={"材料编号": "QDUP", "材料类型": "题目", "题干或内容": content},
            file_id="file-materials",
        ),
        RawMaterialRow(
            row_number=2,
            payload={"材料编号": "QDUP", "材料类型": "题目", "题干或内容": content},
            file_id="file-materials",
        ),
    ]
    item = enrich_sources(materials, material_rows=rows).items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_row_ambiguous"

    source_refs, source_status, source_note = service._project_material_source(
        item, catalog_version="catalog-test"
    )
    assert source_status == "unverified"
    assert source_refs and source_refs[0]["verification_status"] == "unverified"
    assert "source_row_number" not in source_refs[0]
    assert "quote" not in source_refs[0]
    assert "无法唯一定位" in source_note


def test_empty_source_stays_unverified_and_is_not_backfilled() -> None:
    material = MaterialInput(
        material_id="QEMPTY",
        material_type="question",
        content="目录里没有任何出处信息的题",
    )
    item = enrich_sources([material], material_rows=()).items[0]
    assert item.verification_status == "unverified"
    assert item.source_status == "unknown"
    assert item.source_ref()["verification_status"] == "unverified"

    source_refs, source_status, source_note = service._project_material_source(
        item, catalog_version="catalog-test"
    )
    assert source_refs == []
    assert source_status == "unknown"
    assert "找不到" in source_note


def test_exported_materials_carry_verified_row_evidence(client: TestClient) -> None:
    receipt = _import(client)
    dataset_id = receipt["dataset_id"]
    headers = _teacher(client, dataset_id)
    run_id = client.get(
        f"/api/v1/datasets/{dataset_id}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]

    archive = _export(client, headers, run_id)
    with archive:
        materials = _items(archive, "materials_index.json")
    by_id = {material["material_id"]: material for material in materials}

    for material_id in ("Q1", "Q2", "Q3", "Q4", "C1"):
        material = by_id[material_id]
        assert material["source_status"] == "verified"
        assert material["source_note"] == "材料编号在原始材料表中唯一命中，题干逐字一致"
        assert len(material["source_refs"]) == 1
        ref = material["source_refs"][0]
        assert ref["verification_status"] == "verified"
        assert ref["locator_level"] == "structured"
        assert ref["material_id"] == material_id
        assert ref["source_file"] == "题目与材料"
        assert isinstance(ref["source_row_number"], int)
        assert ref["source_row_number"] >= 1
        assert ref["quote"]
        assert ref["file_id"]

    textbook = by_id["B1"]
    assert textbook["source_status"] == "unverified"
    assert textbook["source_note"] == "教材来源文件没有可用的登记 file_id，无法做页码核验"
    assert textbook["source_refs"]
    ref = textbook["source_refs"][0]
    assert ref["verification_status"] == "unverified"
    assert "pdf_page" not in ref
    assert "source_row_number" not in ref
    assert "quote" not in ref

    # 来源模块只改出处投影，不改变知识点关联。
    assert all(material["kp_refs"] for material in materials)

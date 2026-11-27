from __future__ import annotations

from types import SimpleNamespace

from study_companion.sources import MaterialInput, RawMaterialRow, enrich_sources


def _material(
    *,
    material_id: str = "Q001",
    material_type: str = "question",
    content: str = "每支笔x元，买3支笔另付2元包装费，共需多少元？用代数式表示。",
    source_locator: dict | None = None,
    source_label: str | None = "赛事新编",
) -> MaterialInput:
    return MaterialInput(
        material_id=material_id,
        material_type=material_type,
        content=content,
        source_locator=source_locator
        if source_locator is not None
        else {
            "source_file": "赛事新编",
            "verification_status": "unverified",
            "source_kind": "competition_material",
        },
        source_label=source_label,
    )


def _row(
    *,
    row_number: int = 1,
    material_id: str = "Q001",
    content: str = "每支笔x元，买3支笔另付2元包装费，共需多少元？用代数式表示。",
    material_type: str = "题目",
    file_id: str | None = "file-materials",
    source_file: str = "题目与材料",
    declared_row_number: int | None = None,
) -> RawMaterialRow:
    payload: dict[str, object] = {
        "材料编号": material_id,
        "材料类型": material_type,
        "题干或内容": content,
        "来源或资料文件": "赛事新编",
    }
    if declared_row_number is not None:
        payload["source_row_number"] = str(declared_row_number)
    return RawMaterialRow(
        row_number=row_number,
        payload=payload,
        file_id=file_id,
        source_file=source_file,
    )


def test_exact_material_id_match_verifies_and_exports_row_evidence() -> None:
    report = enrich_sources([_material()], material_rows=[_row()])

    assert len(report.items) == 1
    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.reason == "matched_by_material_id"
    assert item.source_file == "题目与材料"
    assert item.source_row_number == 1
    assert item.material_id == "Q001"
    assert item.quote == "每支笔x元，买3支笔另付2元包装费，共需多少元？用代数式表示。"
    assert item.file_id == "file-materials"
    assert item.pdf_page is None

    ref = item.source_ref(catalog_version="catalog-1")
    assert ref["kind"] == "material_source"
    assert ref["material_id"] == "Q001"
    assert ref["source_row_number"] == 1
    assert ref["quote"] == item.quote
    assert ref["verification_status"] == "verified"
    assert ref["locator_level"] == "structured"
    assert ref["catalog_version"] == "catalog-1"


def test_exact_content_match_without_id_verifies() -> None:
    material = _material(material_id="UNKNOWN")
    report = enrich_sources([material], material_rows=[_row()])

    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.reason == "matched_by_content"
    assert item.source_row_number == 1


def test_near_match_with_same_id_is_rejected() -> None:
    material = _material(content="每支笔x元，买3支笔另付2元包装费，共需多少元？用代数式表示")
    report = enrich_sources([material], material_rows=[_row()])

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_content_mismatch"
    assert item.source_row_number is None
    assert item.quote is None
    assert item.source_file == "赛事新编"


def test_near_match_without_id_is_rejected() -> None:
    material = _material(material_id="UNKNOWN", content="每支笔x元，买3支笔另付3元包装费，共需多少元？用代数式表示。")
    report = enrich_sources([material], material_rows=[_row()])

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_row_not_found"
    assert item.source_row_number is None


def test_duplicate_material_id_is_rejected() -> None:
    rows = [
        _row(row_number=1, content="第一行"),
        _row(row_number=2, content="第二行"),
    ]
    material = _material(content="第一行")
    report = enrich_sources([material], material_rows=rows)

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_row_ambiguous"
    assert item.source_row_number is None


def test_duplicate_content_is_rejected() -> None:
    rows = [
        _row(row_number=1, material_id="Q001", content="完全相同的题干"),
        _row(row_number=2, material_id="Q002", content="完全相同的题干"),
    ]
    material = _material(material_id="UNKNOWN", content="完全相同的题干")
    report = enrich_sources([material], material_rows=rows)

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_row_ambiguous"
    assert item.source_row_number is None


def test_material_type_mismatch_is_rejected() -> None:
    material = _material(material_type="question")
    report = enrich_sources([material], material_rows=[_row(material_type="教材")])

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "material_type_mismatch"


def test_declared_row_number_conflict_is_rejected() -> None:
    material = _material()
    report = enrich_sources(
        [material],
        material_rows=[_row(row_number=1, declared_row_number=2)],
    )

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.reason == "row_number_inconsistent"


def test_no_source_marks_unknown_and_keeps_unverified_contract() -> None:
    material = MaterialInput(
        material_id="Q999",
        material_type="question",
        content="没有来源的题目",
        source_locator={},
        source_label=None,
    )
    report = enrich_sources([material], material_rows=[])

    item = report.items[0]
    assert item.verification_status == "unverified"
    assert item.source_status == "unknown"
    assert item.reason == "material_row_not_found"
    assert item.source_file is None
    assert item.source_row_number is None
    assert item.quote is None
    assert item.pdf_page is None
    assert item.source_ref()["source_file"] == ""
    assert item.source_ref()["verification_status"] == "unverified"


def test_card_is_verified_against_card_row() -> None:
    material = _material(
        material_id="L01",
        material_type="card",
        content="代数式与整式运算：先看数量关系。",
    )
    row = _row(
        material_id="L01",
        material_type="学习卡",
        content="代数式与整式运算：先看数量关系。",
    )
    report = enrich_sources([material], material_rows=[row])

    item = report.items[0]
    assert item.verification_status == "verified"
    assert item.reason == "matched_by_material_id"
    assert item.source_row_number == 1


def test_raw_material_row_can_be_built_from_source_row() -> None:
    source_row = SimpleNamespace(
        row_number=3,
        payload_json={
            "材料编号": "Q001",
            "材料类型": "题目",
            "题干或内容": "第一题",
        },
        file_id="file-materials",
        dataset_id="ds-1",
        table_name="materials",
    )
    row = RawMaterialRow.from_source_row(source_row)

    assert row.row_number == 3
    assert row.material_id == "Q001"
    assert row.content == "第一题"
    assert row.file_id == "file-materials"
    assert row.dataset_id == "ds-1"


def test_summary_counts_and_reason_grouping() -> None:
    rows = [
        _row(row_number=1, material_id="Q001", content="第一题"),
        _row(row_number=2, material_id="Q002", content="第二题"),
    ]
    materials = [
        _material(material_id="Q001", content="第一题"),
        _material(material_id="Q002", content="第二题改了"),
        _material(material_id="Q003", content="第三题"),
    ]
    report = enrich_sources(materials, material_rows=rows)

    assert report.by_status() == {"verified": 1, "unverified": 2, "not_applicable": 0}
    assert report.by_reason() == {
        "matched_by_material_id": 1,
        "material_content_mismatch": 1,
        "material_row_not_found": 1,
    }
    assert report.reasons()["material_row_not_found"] == ["Q003"]
    assert report.by_material_id()["Q001"].verification_status == "verified"
    refs = report.source_refs_by_material_id(catalog_version="catalog-1")
    assert refs["Q001"]["source_row_number"] == 1
    assert refs["Q001"]["catalog_version"] == "catalog-1"

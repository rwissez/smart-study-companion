from __future__ import annotations

from study_companion.sources import (
    AuditInputs,
    MaterialInput,
    RawMaterialRow,
    build_audit_report,
    enrich_sources,
)


def _material(material_id: str, material_type: str, content: str) -> MaterialInput:
    return MaterialInput(
        material_id=material_id,
        material_type=material_type,
        content=content,
        source_locator={"source_file": "题目与材料", "verification_status": "unverified"},
    )


def test_build_audit_report_counts_and_preserves_unknown_kp() -> None:
    inputs = AuditInputs(
        materials=(
            _material("Q001", "question", "第一题"),
            _material("Q002", "question", "第二题"),
            _material("B01", "textbook", "七年级下册 第八章 实数"),
        ),
        material_rows=(
            RawMaterialRow(
                row_number=1,
                payload={
                    "材料编号": "Q001",
                    "材料类型": "题目",
                    "题干或内容": "第一题",
                },
                file_id="file-materials",
            ),
        ),
        annotations=(
            {
                "annotation_id": "ann-Q001",
                "source_type": "material",
                "source_id": "Q001",
                "target_type": "knowledge_point",
                "target_ids": ["kp-T01I1"],
                "knowledge_role": "primary",
                "basis": "standard",
            },
        ),
        knowledge_version="kn-1",
        knowledge_point_ids=("kp-T01I1",),
        points=(
            {
                "kp_id": "kp-T01I1",
                "source_locators": [
                    {"source_file": "课程要求.csv", "verification_status": "verified"}
                ],
            },
        ),
        dataset_id="ds-1",
        catalog_version="cat-1",
    )

    report = build_audit_report(inputs)

    assert report["read_only"] is True
    assert report["materials"]["total"] == 3
    assert report["materials"]["verified"] == 1
    assert report["materials"]["unverified"] == 2
    assert report["materials"]["not_applicable"] == 0
    assert report["materials"]["by_material_type"]["question"] == {
        "total": 2,
        "verified": 1,
        "unverified": 1,
        "not_applicable": 0,
    }
    assert report["materials"]["by_material_type"]["textbook"] == {
        "total": 1,
        "verified": 0,
        "unverified": 1,
        "not_applicable": 0,
    }
    assert report["knowledge"]["materials_mapped"] == 1
    assert report["knowledge"]["materials_unknown"] == 2
    assert report["knowledge"]["unknown_material_ids"] == ["B01", "Q002"]
    assert report["knowledge"]["points"]["points_total"] == 1
    assert report["knowledge"]["points"]["locators_verified"] == 1

    items = {item["material_id"]: item for item in report["items"]}
    assert items["Q001"]["verification_status"] == "verified"
    assert items["Q001"]["kp_status"] == "mapped"
    assert items["Q002"]["kp_status"] == "unknown"
    assert items["B01"]["reason"] == "textbook_file_not_registered"


def test_build_audit_report_can_omit_items() -> None:
    inputs = AuditInputs(
        materials=(_material("Q001", "question", "第一题"),),
        material_rows=(),
    )
    report = build_audit_report(inputs, include_items=False)
    assert "items" not in report
    assert report["materials"]["unverified"] == 1


def test_quote_preview_is_bounded_in_audit_items() -> None:
    content = "长" * 200
    report = build_audit_report(
        AuditInputs(
            materials=(_material("Q001", "question", content),),
            material_rows=(
                RawMaterialRow(
                    row_number=1,
                    payload={
                        "材料编号": "Q001",
                        "材料类型": "题目",
                        "题干或内容": content,
                    },
                ),
            ),
        )
    )
    item = report["items"][0]
    assert item["verification_status"] == "verified"
    assert len(item["quote_preview"]) == 120
    assert item["quote_length"] == 200


def test_enrichment_report_does_not_mutate_inputs() -> None:
    material = _material("Q001", "question", "第一题")
    rows = (
        RawMaterialRow(
            row_number=1,
            payload={"材料编号": "Q001", "材料类型": "题目", "题干或内容": "第一题"},
        ),
    )
    before = material.source_locator.copy()
    enrich_sources([material], material_rows=rows)
    assert material.source_locator == before

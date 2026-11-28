from __future__ import annotations

from study_companion.sources import (
    KnowledgePointSource,
    MaterialInput,
    project_knowledge_refs,
    project_knowledge_sources,
    summarize_knowledge_refs,
    summarize_knowledge_sources,
)


def _material(
    material_id: str,
    *,
    indicator_ids: tuple[str, ...] = (),
) -> MaterialInput:
    return MaterialInput(
        material_id=material_id,
        material_type="question",
        content="",
        indicator_ids=indicator_ids,
    )


def _annotation(
    *,
    annotation_id: str = "ann-Q001",
    source_id: str = "Q001",
    target_ids: tuple[str, ...] = ("kp-T01I1",),
    locators: tuple[dict, ...] = (),
) -> dict:
    return {
        "annotation_id": annotation_id,
        "source_type": "material",
        "source_id": source_id,
        "target_type": "knowledge_point",
        "target_ids": list(target_ids),
        "knowledge_role": "primary",
        "basis": "standard",
        "source_locators": list(locators),
    }


def test_formal_annotation_projects_to_kp_ref_without_new_links() -> None:
    locator = {"source_file": "课程要求.csv", "verification_status": "verified"}
    projections = project_knowledge_refs(
        [_material("Q001")],
        annotations=[_annotation(locators=(locator,))],
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
    )

    assert len(projections) == 1
    projection = projections[0]
    assert projection.status == "mapped"
    assert projection.note == ""
    assert len(projection.refs) == 1
    ref = projection.refs[0]
    assert ref.kp_id == "kp-T01I1"
    assert ref.role == "primary"
    assert ref.basis == "standard"
    assert ref.annotation_id == "ann-Q001"
    assert ref.knowledge_version == "kn-1"
    assert ref.current is True
    assert ref.source == "annotation"
    assert ref.source_locators == (locator,)


def test_missing_annotation_stays_unknown_and_is_not_invented() -> None:
    projections = project_knowledge_refs(
        [_material("B01", indicator_ids=("T01I1",))],
        annotations=[],
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
    )

    projection = projections[0]
    assert projection.status == "unknown"
    assert projection.refs == ()
    assert "没有正式材料-知识点标注" in projection.note

    summary = summarize_knowledge_refs(projections)
    assert summary["materials_mapped"] == 0
    assert summary["materials_unknown"] == 1
    assert summary["unknown_material_ids"] == ["B01"]


def test_indicator_fallback_is_opt_in() -> None:
    material = _material("B01", indicator_ids=("T01I1",))
    baseline = project_knowledge_refs(
        [material],
        annotations=[],
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
    )
    assert baseline[0].status == "unknown"

    with_fallback = project_knowledge_refs(
        [material],
        annotations=[],
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
        include_indicator_fallback=True,
    )
    assert with_fallback[0].status == "mapped"
    assert with_fallback[0].refs[0].source == "indicator_mapping"
    assert with_fallback[0].refs[0].annotation_id is None


def test_rejected_annotation_and_missing_target_are_not_projected() -> None:
    rejected = _annotation(annotation_id="ann-rejected")
    rejected["review_info"] = {"status": "rejected"}
    missing_target = _annotation(
        annotation_id="ann-missing",
        source_id="Q002",
        target_ids=("kp-not-in-graph",),
    )

    projections = project_knowledge_refs(
        [_material("Q001"), _material("Q002")],
        annotations=[rejected, missing_target],
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
    )

    assert projections[0].status == "unknown"
    assert projections[1].status == "unknown"
    assert projections[1].unresolved_kp_ids == ("kp-not-in-graph",)
    assert "不在当前知识版本" in projections[1].note


def test_multiple_annotations_deduplicate_by_kp_id() -> None:
    annotations = [
        _annotation(annotation_id="ann-1"),
        _annotation(annotation_id="ann-2"),
    ]
    projections = project_knowledge_refs(
        [_material("Q001")],
        annotations=annotations,
        knowledge_version="kn-1",
        knowledge_point_ids={"kp-T01I1"},
    )

    assert len(projections[0].refs) == 1
    assert projections[0].refs[0].annotation_id == "ann-1"


def test_knowledge_point_sources_project_existing_locators_only() -> None:
    points = [
        {
            "kp_id": "kp-T01I1",
            "source_locators": [
                {"source_file": "课程要求.csv", "verification_status": "verified"},
                {"source_file": "旧页", "verification_status": "rejected"},
            ],
        },
        {
            "kp_id": "kp-T01I2",
            "source_locators": [
                {"source_file": "课程要求.csv", "verification_status": "unverified"},
            ],
        },
        {"kp_id": "kp-T01I3", "source_locators": []},
    ]
    sources = project_knowledge_sources(points)

    assert isinstance(sources[0], KnowledgePointSource)
    assert len(sources[0].source_locators) == 1
    assert sources[0].source_locators[0]["verification_status"] == "verified"

    summary = summarize_knowledge_sources(sources)
    assert summary == {
        "points_total": 3,
        "points_with_any_locator": 2,
        "points_with_verified_locator": 1,
        "locators_total": 2,
        "locators_verified": 1,
        "locators_unverified": 1,
    }

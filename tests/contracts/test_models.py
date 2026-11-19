import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from study_companion.contracts import (
    AnalysisBundle,
    Catalog,
    PreparedDataset,
    RawTables,
    RecommendationBundle,
    StudentResult,
    EvidenceRef,
    MetricScope,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_contracts.json"


def test_synthetic_fixture_validates_for_b_and_c():
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert RawTables.model_validate(sample["raw_tables"]).students[0]["学号"] == "S01"
    prepared = PreparedDataset.model_validate(sample["prepared_dataset"])
    assert len(prepared.selected_events) == 5
    catalog = Catalog.model_validate(sample["catalog"])
    assert catalog.version == "synthetic-v1"
    analysis = AnalysisBundle.model_validate(sample["analysis_bundle"])
    assert len(analysis.student_results) == 1
    recommendation = RecommendationBundle.model_validate(sample["recommendation_bundle"])
    assert recommendation.student_id == analysis.student_results[0].student_id


def test_fixture_traceability_and_class_results():
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prepared = PreparedDataset.model_validate(sample["prepared_dataset"])
    analysis = AnalysisBundle.model_validate(sample["analysis_bundle"])
    records = {record.record_id: record for record in prepared.raw_records}
    events = {event.event_id: event for event in prepared.selected_events}
    assert prepared.source_manifest.table_row_counts["records"] == len(records) == len(sample["raw_tables"]["records"])
    for record in records.values():
        raw = sample["raw_tables"]["records"][record.source_row_number - 1]
        assert record.payload == {key: value for key, value in raw.items() if key != "source_row_number"}
    for event in events.values():
        if event.selected_record_id:
            record = records[event.selected_record_id]
            assert record.payload["学号"] == event.student_id
            assert record.payload["节点编号"] == event.node_id
    assert records["R01"].payload["修订版本"] == "1"
    assert events["E01"].selected_record_id == "R02"
    assert records["R02"].payload["修订版本"] == "2"
    assert events["E02"].disposition == "exclude_score"
    assert events["E03"].status == "pending"
    assert events["E04"].status == "unanswered"
    conflict = next(issue for issue in prepared.quality_issues if issue.code == "CONFLICTING_RECORDS")
    assert conflict.record_ids == ["R06", "R07"]
    assert events[conflict.event_id].disposition == "quarantine_event"
    assert events[conflict.event_id].selected_record_id is None
    for issue in prepared.quality_issues:
        assert set(issue.record_ids) <= records.keys()
        assert issue.event_id in events
    result = analysis.class_results[0]
    assert result.roster_count == sum(s.class_id == result.class_id for s in prepared.students)
    assert result.observed_student_count == 1
    metric = result.metrics[0]
    assert metric.value == metric.numerator / metric.denominator
    assert metric.scope.class_id == result.class_id
    assert metric.evidence_refs[0].id == events["E01"].selected_record_id
    assert metric.evidence_refs[0].run_id == analysis.run_id


def test_recommendation_states_are_diverse_and_grounded():
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prepared = PreparedDataset.model_validate(sample["prepared_dataset"])
    catalog = Catalog.model_validate(sample["catalog"])
    analysis = AnalysisBundle.model_validate(sample["analysis_bundle"])
    bundle = RecommendationBundle.model_validate(sample["recommendation_bundle"])

    findings = {
        finding.finding_id: finding
        for result in analysis.student_results
        for finding in result.findings
    }
    metrics = {
        metric.metric_id: metric
        for result in analysis.student_results
        for metric in result.metrics
    }
    records = {record.record_id for record in prepared.raw_records}
    materials = {material.material_id for material in catalog.materials}
    recommendations = {item.recommendation_id: item for item in bundle.recommendations}

    assert {item.review_status for item in bundle.recommendations} == {
        "draft",
        "validated",
        "needs_review",
    }
    assert {item.generation_method for item in bundle.recommendations} == {"rule", "model"}
    assert len(recommendations) == 4

    for recommendation in bundle.recommendations:
        assert set(recommendation.finding_ids) <= findings.keys()
        assert {activity.material_id for activity in recommendation.activities} <= materials

    for issue in bundle.validation_issues:
        assert issue.recommendation_id in recommendations

    finding = findings["F02"]
    assert finding.performance == "needs_practice"
    assert finding.evidence_status == "sufficient"
    record_refs = {ref.id for ref in finding.evidence_refs if ref.kind == "record"}
    metric_refs = {ref.id for ref in finding.evidence_refs if ref.kind == "metric"}
    assert record_refs == {"R01", "R02"}
    assert record_refs <= records
    assert metric_refs == {"M_S01"}
    assert metric_refs <= metrics.keys()

    assert recommendations["REC02"].activities[0].previously_attempted is False
    assert bundle.validation_issues[0].code == "WEAK_EVIDENCE_BASIS"
    assert bundle.validation_issues[0].recommendation_id == "REC04"


def test_student_result_requires_run_identity():
    with pytest.raises(ValidationError):
        StudentResult.model_validate({"student_id": "S01", "node_id": "N01", "metrics": [], "findings": [], "limitations": []})


def test_raw_table_rejects_non_string_payload_values():
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))["raw_tables"]
    sample["records"][0]["分数"] = 3
    with pytest.raises(ValidationError):
        RawTables.model_validate(sample)


def test_evidence_requires_the_correct_version_owner():
    with pytest.raises(ValidationError):
        EvidenceRef(kind="record", id="R01")
    with pytest.raises(ValidationError):
        EvidenceRef(kind="material", id="Q01")
    assert EvidenceRef(kind="record", id="R01", run_id="run-1").run_id == "run-1"


def test_metric_scope_has_exactly_one_subject():
    with pytest.raises(ValidationError):
        MetricScope(node_id="N01")
    with pytest.raises(ValidationError):
        MetricScope(node_id="N01", student_id="S01", class_id="C01")

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

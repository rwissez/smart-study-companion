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
    assert len(prepared.selected_events) == 4
    catalog = Catalog.model_validate(sample["catalog"])
    assert catalog.version == "synthetic-v1"
    analysis = AnalysisBundle.model_validate(sample["analysis_bundle"])
    assert len(analysis.student_results) == 1
    recommendation = RecommendationBundle.model_validate(sample["recommendation_bundle"])
    assert recommendation.student_id == analysis.student_results[0].student_id


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

"""班级共性与重点关注学生（03 §12）。"""

from __future__ import annotations

from study_companion.analysis.class_insights import (
    build_attention_students,
    build_common_findings,
    build_distribution,
    enrich_class,
)
from study_companion.contracts import EvidenceRef, Finding, MetricScope, StudentResult


def _finding(
    finding_id: str,
    indicator: str,
    performance: str,
    evidence: str,
    *,
    student: str = "S01",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        indicator_id=indicator,
        description=f"{student} 的 {indicator}",
        evidence_status=evidence,  # type: ignore[arg-type]
        performance=performance,  # type: ignore[arg-type]
        evidence_refs=[EvidenceRef(kind="record", id=f"R-{student}-{indicator}", run_id="run-1")],
        limitations=[],
        generation_method="rule",
    )


def _member(student_id: str, findings: list[Finding]) -> StudentResult:
    return StudentResult(
        run_id="run-1",
        student_id=student_id,
        dataset_id="ds-1",
        class_id="DC01",
        node_id="N01",
        catalog_version="cat-1",
        metrics=[],
        findings=findings,
        limitations=[],
        analysis_version=f"ban-{student_id}",
    )


def test_common_findings_need_half_of_observed_members() -> None:
    members = [
        _member("S01", [_finding("f-1", "I1", "needs_practice", "sufficient")]),
        _member("S02", [_finding("f-2", "I1", "needs_practice", "sufficient")]),
        _member("S03", [_finding("f-3", "I1", "observed_strength", "sufficient")]),
        # 证据不足的成员不进分母
        _member("S04", [_finding("f-4", "I1", "undetermined", "insufficient")]),
    ]
    common, limitations = build_common_findings(class_id="DC01", node_id="N01", members=members)
    assert len(common) == 1
    assert common[0].indicator_id == "I1"
    assert "2/3" in common[0].description
    assert common[0].evidence_status == "sufficient"
    assert not limitations


def test_small_class_does_not_claim_common_problem() -> None:
    members = [_member("S01", [_finding("f-1", "I1", "needs_practice", "sufficient")])]
    common, limitations = build_common_findings(class_id="DC01", node_id="N01", members=members)
    assert common == []
    assert any("不足2人" in item for item in limitations)


def test_distribution_counts_every_finding_with_roster_denominator() -> None:
    members = [
        _member("S01", [_finding("f-1", "I1", "needs_practice", "sufficient")]),
        _member("S02", [_finding("f-2", "I1", "undetermined", "insufficient")]),
    ]
    buckets = build_distribution(members=members, roster_count=3)
    assert sum(item.count for item in buckets) == 2
    assert all(item.roster_count == 3 for item in buckets)


def test_attention_students_cover_weak_conflict_and_empty_evidence() -> None:
    weak = _member("S01", [_finding("f-1", "I1", "needs_practice", "sufficient")])
    conflict = _member("S02", [_finding("f-2", "I2", "undetermined", "conflicting")])
    none = _member("S03", [_finding("f-3", "I3", "undetermined", "insufficient")])
    healthy = _member("S04", [_finding("f-4", "I1", "observed_strength", "sufficient")])
    attention = build_attention_students(members=[weak, conflict, none, healthy])
    ids = {item.student_id for item in attention}
    assert ids == {"S01", "S02", "S03"}
    reasons = {item.student_id: item.reason for item in attention}
    assert "需要加强" in reasons["S01"]
    assert "记录冲突" in reasons["S02"]
    assert "充分证据" in reasons["S03"]


def test_enrich_class_returns_all_three_outputs() -> None:
    members = [
        _member("S01", [_finding("f-1", "I1", "needs_practice", "sufficient")]),
        _member("S02", [_finding("f-2", "I1", "needs_practice", "sufficient")]),
    ]
    result = enrich_class(class_id="DC01", node_id="N01", roster_count=2, members=members)
    assert result["common_findings"]
    assert result["performance_distribution"]
    assert result["attention_students"]
